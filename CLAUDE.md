# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Clio Store is a FastAPI server for managing connectomics/EM (electron microscopy) datasets. Deployed on Google Cloud Run.

- **Framework**: FastAPI with **Pydantic v1** (1.10.x)
- **Data stores**: Google Firestore, BigQuery, Cloud Storage
- **Auth**: DatasetGateway (DSG) — `DSG_URL` is required
- **Server**: Hypercorn (production, HTTP/2), Uvicorn (development)
- **Language**: Python 3.12, dependencies via [pixi](https://pixi.sh)

## Development Commands

```bash
# Install dependencies
pixi install

# Configure local .env (interactive; --use-env skips prompts for keys already set)
pixi run setup

# Run locally over plain HTTP (uvicorn, --reload)
pixi run dev

# Run locally over HTTPS with HTTP/2 + access logs (hypercorn, --reload)
pixi run dev --certs ../certs

# Run tests
pixi run pytest    # or: pytest

# Deploy to Cloud Run (interactive; --dry-run to preview)
pixi run deploy
```

`pixi run dev` reads `.env` and execs uvicorn (or hypercorn with TLS when
`--certs DIR` is given, expecting `DIR/localhost+2.pem` and
`DIR/localhost+2-key.pem` from mkcert). See README.md for the mkcert setup.

A pytest suite lives under `tests/` (DSG auth integration). No linter or
CI/CD pipeline is configured.

## Architecture

### Core Files

- `main.py` — Route wiring; mounts service routers at `/v2/` and `/test/`. The DSG browser-auth router (`services/auth.py`) is mounted at the root (`/login`, `/profile`, `/logout`).
- `config.py` — Env var constants and Firestore collection names (imported via `from config import *`). Exits at import time if `DSG_URL` isn't set.
- `dependencies.py` — `User` model, `Dataset` model, `DatasetCache`, DSG auth (`_get_user_from_dsg`, `_dsg_group_members`), `get_user` dependency, and the FastAPI `app` instance with CORS middleware.

### Auth Flow

`oauth2_scheme` → `get_user_from_token(request, token)` → `_get_user_from_dsg(...)` calls `{DSG_URL}/api/v1/user/cache` and maps the response to a `User`. There is no legacy/Firestore auth path — `DSG_URL` is required.

Token resolution checks: `Authorization: Bearer` header → `dsg_token` cookie → `dsg_token` query param.

See `docs/dsg-integration.md` for the full DatasetGateway integration design (permission mapping, the `public` flag interaction, group members, user management endpoint behavior, token generation proxying). `docs/DatasetGatewayIntegration.md` covers frontend/browser-auth patterns.

### DatasetGateway Permission Mapping

DatasetGateway → clio-store roles:
- `admin: true` → `global_roles: {"admin"}`
- `permissions_v2[ds]` has `"view"` → `datasets[ds]` has `"clio_general"`
- `permissions_v2[ds]` has `"edit"` → `datasets[ds]` has `"clio_write"`
- `datasets_admin` includes `ds` → `datasets[ds]` has `"dataset_admin"`

Access is granted from **two sources**: DatasetGateway permissions AND the Firestore `public` flag. A user with no DSG permissions can still access a public dataset. The `public` flag stays in Firestore (not DSG) and is loaded by `DatasetCache`.

DSG-related endpoints:
- `GET/POST/DELETE /v2/users` → 501 (manage users via DatasetGateway instead)
- `POST /v2/server/token` proxies to `{DSG_URL}/api/v1/create_token`
- Group membership is fetched from `{DSG_URL}/api/v1/groups/{name}/members`
- `/login`, `/profile`, `/logout` (top-level, not under `/v2/`) handle the browser-auth redirect dance

### Role System

- **Global roles**: `admin`, `clio_general`, `clio_write`
- **Per-dataset roles**: stored in `User.datasets[dataset_name]` as a set of role strings
- **Key methods on `User`**: `has_role()`, `can_read()`, `can_write_own()`, `can_write_others()`, `is_dataset_admin()`, `is_admin()`
- `OWNER` env var email automatically gets `admin`
- Datasets with `public=True` grant `clio_general` access to all authenticated users

### Caching

- `DatasetCache` in `dependencies.py` — initialized at startup; refreshes from Firestore every 600s
- `_dsg_user_cache` and `_dsg_group_members_cache` — in-memory dicts, 600s TTL, keyed by token / group name
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

Exceptions:
- `volumes` is mounted without `Depends(get_user)`.
- `services/auth.py` is mounted only once at the root (no `/v2/` or `/test/` prefix), since browser-facing OAuth routes don't follow the API versioning convention.

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

Local dev reads these from `.env` (managed by `pixi run setup`).
See `.env.example` for the full list and defaults.

| Variable | Required | Description |
|----------|----------|-------------|
| `DSG_URL` | yes | DatasetGateway base URL — all auth/authz delegates here |
| `OWNER` | yes | Email that automatically gets global `admin` privileges |
| `URL_PREFIX` |  | Prefix before all API endpoints (default: empty) |
| `ALLOWED_ORIGINS` |  | CORS allowed origins (default: `*`) |
| `SIG_BUCKET` |  | Cloud Storage bucket for image signature queries |
| `TRANSFER_FUNC` |  | Cloud Function URL for image transfer |
| `TRANSFER_DEST` |  | Destination cache for image transfers |
| `GOOGLE_APPLICATION_CREDENTIALS` |  | GCP credentials path (local dev only — Cloud Run uses workload identity) |
| `CLIO_SUBVOL_BUCKET` |  | Cloud Storage bucket for subvolume edits |
| `CLIO_SUBVOL_WIDTH` |  | Subvolume width (default: 256) |

## Deployment Notes

Production uses Hypercorn for HTTP/2 support, which is required to avoid Google Cloud Run's 32 MiB response size limit on HTTP/1. The Dockerfile runs on port 8080 by default. `scripts/deploy.py` (`pixi run deploy`) prompts for deploy-time settings, persists them to `.env`, and runs `gcloud run deploy --use-http2` with the runtime env vars passed via `--set-env-vars`.
