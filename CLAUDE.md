# Clio Store

FastAPI server for managing connectomics/EM (electron microscopy) datasets. Deployed on Google Cloud Run.

## Tech Stack

- **Framework**: FastAPI (with Pydantic v1)
- **Data stores**: Google Firestore, BigQuery, Cloud Storage
- **Auth**: Google OAuth2 + FlyEM JWT, or DatasetGateway (when `DSG_URL` is set)
- **Server**: Hypercorn (production), Uvicorn (development)
- **Language**: Python 3.12

## Running Locally

```bash
uvicorn main:app --reload
# or
hypercorn main:app --bind 0.0.0.0:8000 --reload
```

## Project Structure

- `main.py` — Route wiring; includes all service routers with `/v2/` and `/test/` prefixes
- `config.py` — Environment variables and Firestore collection name constants
- `dependencies.py` — Core models (`User`, `Dataset`, caches), auth middleware, `get_user` dependency, FastAPI `app` instance
- `services/` — API routers, one module per endpoint category (annotations, datasets, kv, volumes, etc.)
- `stores/` — Data access layer (Firestore client, caching)
- `Dockerfile` — Cloud Run deployment container

## Architecture

### Auth Flow

Token -> `get_user_from_token()` -> either:
1. **DatasetGateway** (if `DSG_URL` is set): forwards token to DatasetGateway service, returns `User` with roles
2. **Legacy**: Google OAuth2 ID token verification / FlyEM JWT verification -> returns `User` with roles

### Role System

- **Global roles**: `admin`, `clio_general`, `clio_write`
- **Per-dataset roles**: stored on individual dataset documents
- Datasets can be marked `public` (accessible without specific dataset roles)

### Caching

`UserCache` and `DatasetCache` in `dependencies.py` with TTL-based refresh from Firestore.

### Adding a New Service

1. Create a module in `services/` with an `APIRouter`
2. Wire it into `main.py` with `app.include_router(...)` at both `/v2/` and `/test/` prefixes
3. Add `dependencies=[Depends(get_user)]` for auth-protected routes

## Key Conventions

- **Pydantic v1 API**: Uses `root_validator`, `validator`, `BaseModel`. Do NOT use v2 APIs (`model_validator`, `field_validator`, `model_config`, etc.)
- **Auth dependency**: All data routes require `Depends(get_user)`
- **Route prefixes**: Routes are mounted at both `/v2/` and `/test/` prefixes (test is `include_in_schema=False`)
- **Imports**: Use `from typing import ...` for type hints (not `from pydantic.typing`)

## Environment Variables

| Variable | Description |
|----------|-------------|
| `OWNER` | Email that automatically gets global `admin` privileges |
| `DSG_URL` | DatasetGateway URL; when set, auth delegates to DatasetGateway |
| `FLYEM_SECRET` | Secret for issuing FlyEM JWT tokens |
| `TEST_USER` | When set, bypasses auth and uses this email |
| `URL_PREFIX` | Prefix added before all API endpoints (default: empty) |
| `ALLOWED_ORIGINS` | CORS allowed origins (default: `*`) |
| `SIG_BUCKET` | Cloud Storage bucket for image signature queries |
| `NEUPRINT_APPLICATION_CREDENTIALS` | Credentials for neuprint service |
| `TRANSFER_FUNC` | Cloud Function URL for image transfer |
| `TRANSFER_DEST` | Destination for image transfers |
| `CLIO_SUBVOL_BUCKET` | Cloud Storage bucket for subvolume edits |
| `CLIO_SUBVOL_WIDTH` | Subvolume width (default: 256) |
