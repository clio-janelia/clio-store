# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Clio Store is a FastAPI server for managing connectomics/EM (electron microscopy) datasets. Deployed on Google Cloud Run.

- **Framework**: FastAPI with **Pydantic v1** (1.10.x)
- **Data stores**: Google Firestore, BigQuery, Cloud Storage
- **Auth**: Google OAuth2 + FlyEM JWT, or DatasetGateway (when `DSG_URL` is set)
- **Server**: Hypercorn (production, HTTP/2), Uvicorn (development)
- **Language**: Python 3.12

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (development)
uvicorn main:app --reload

# Run locally with HTTP/2 (matches production)
hypercorn main:app --bind 0.0.0.0:8000 --reload

# Deploy to Cloud Run
gcloud run deploy clio-store --source . --region us-east4 --allow-unauthenticated --use-http2

# Local auth bypass (legacy mode only): set TEST_USER=<email> to skip token validation
```

No test suite, linter config, or CI/CD pipeline exists in this repo.

## Architecture

### Core Files

- `main.py` — Route wiring; includes all service routers at `/v2/` and `/test/` prefixes
- `config.py` — Environment variables and Firestore collection name constants (imported via `from config import *`)
- `dependencies.py` — `User` model, `Dataset` model, `UserCache`, `DatasetCache`, auth middleware, `get_user` dependency, and the FastAPI `app` instance

### Auth Flow

`oauth2_scheme` → `get_user_from_token(request, token)` → either:
1. **DatasetGateway** (if `DSG_URL` is set): `_get_user_from_dsg()` calls `{DSG_URL}/api/v1/user/cache`, maps response to `User`
2. **Legacy** (default): `_get_user_from_legacy()` tries FlyEM JWT first (if `FLYEM_SECRET` set), then Google OAuth2 ID token, then `TEST_USER` fallback

Token resolution in DSG mode checks: Bearer header → `dsg_token` cookie → `dsg_token` query param.

See `docs/dsg-integration.md` for the full DatasetGateway integration design, including permission mapping, the `public` flag interaction, group members, user management endpoint behavior, token generation proxying, and migration steps.

### DatasetGateway Permission Mapping

DatasetGateway `view`/`edit` permissions map to clio-store roles:
- `admin: true` → `global_roles: {"admin"}`
- `permissions_v2[ds]` has `"view"` → `datasets[ds]` has `"clio_general"`
- `permissions_v2[ds]` has `"edit"` → `datasets[ds]` has `"clio_write"`
- `datasets_admin` includes `ds` → `datasets[ds]` has `"dataset_admin"`

Access is granted from **two sources**: DatasetGateway permissions AND the Firestore `public` flag. A user with no DSG permissions can still access a public dataset. The `public` flag stays in Firestore (not DSG) and is loaded by `DatasetCache`.

When `DSG_URL` is set:
- `GET/POST/DELETE /v2/users` returns 501 (manage users via DatasetGateway instead)
- `POST /v2/server/token` proxies to `{DSG_URL}/api/v1/create_token`
- Group membership is fetched from `{DSG_URL}/api/v1/groups/{name}/members`

### Role System

- **Global roles**: `admin`, `clio_general`, `clio_write`
- **Per-dataset roles**: stored in `User.datasets[dataset_name]` as a set of role strings
- **Key methods on `User`**: `has_role()`, `can_read()`, `can_write_own()`, `can_write_others()`, `is_dataset_admin()`, `is_admin()`
- `OWNER` env var email automatically gets `admin`
- Datasets with `public=True` grant `clio_general` access to all authenticated users

### Caching

- `DatasetCache` in `dependencies.py` — always initialized; refreshes from Firestore every 600s
- `UserCache` in `dependencies.py` — only initialized in legacy mode (`DSG_URL` unset); set to `None` when DSG is enabled
- `_dsg_user_cache` and `_dsg_group_members_cache` — in-memory dicts used only in DatasetGateway mode, 600s TTL
- `DocumentCache` in `stores/cache.py` — per-document caching with 120s staleness threshold

### Data Access

Firestore paths are built with `firestore.get_collection([CLIO_COLLECTION, dataset, subcol])`. Collection name constants are defined in `config.py` (e.g., `CLIO_ANNOTATIONS_V2`, `CLIO_KEYVALUE`).

## Key Conventions

### Pydantic v1 Only

This project pins `pydantic==1.10.x`. Do NOT use v2 APIs:
- Use `@root_validator`, `@validator` (not `@model_validator`, `@field_validator`)
- Use `model.dict()` (not `model.model_dump()`)
- Use `class Config:` (not `model_config = ...`)

### Route Registration

Every service router is mounted twice in `main.py`:
```python
app.include_router(svc.router, prefix=f"{URL_PREFIX}/v2/endpoint", dependencies=[Depends(get_user)])
app.include_router(svc.router, prefix=f"{URL_PREFIX}/test/endpoint", dependencies=[Depends(get_user)], include_in_schema=False)
```

The `volumes` router is the only one without `Depends(get_user)`.

### Adding a New Service

1. Create a module in `services/` with an `APIRouter`
2. Wire it into `main.py` at both `/v2/` and `/test/` prefixes with `dependencies=[Depends(get_user)]`

### Import Patterns

```python
from typing import List, Dict, Set, Optional, Any, Mapping  # type hints
from pydantic import BaseModel, root_validator, validator     # Pydantic v1
from config import *                                          # env var constants
from dependencies import get_user, User, Dataset, group_members
from stores import firestore, cache
```

### Route Pattern

Routes typically define both with and without trailing slash. Auth errors use `HTTPException(status_code=401, detail="...")`.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `OWNER` | Email that automatically gets global `admin` privileges |
| `DSG_URL` | DatasetGateway URL; when set, auth delegates to DatasetGateway |
| `FLYEM_SECRET` | Secret for FlyEM JWT token validation |
| `TEST_USER` | When set, bypasses auth and uses this email |
| `URL_PREFIX` | Prefix before all API endpoints (default: empty) |
| `ALLOWED_ORIGINS` | CORS allowed origins (default: `*`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | GCP service account credentials (local dev) |
| `SIG_BUCKET` | Cloud Storage bucket for image signature queries |
| `NEUPRINT_APPLICATION_CREDENTIALS` | Credentials for neuprint service |
| `TRANSFER_FUNC` | Cloud Function URL for image transfer |
| `TRANSFER_DEST` | Destination for image transfers |
| `CLIO_SUBVOL_BUCKET` | Cloud Storage bucket for subvolume edits |
| `CLIO_SUBVOL_WIDTH` | Subvolume width (default: 256) |

## Deployment Notes

Production uses Hypercorn for HTTP/2 support, which is required to avoid Google Cloud Run's 32 MiB response size limit on HTTP/1. The Dockerfile runs on port 8080 by default.
