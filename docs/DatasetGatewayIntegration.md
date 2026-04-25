# DatasetGateway Browser Auth Integration

## Context

clio-store already supports DSG token validation for API calls (Bearer header, `dsg_token` cookie, query param) via `_get_user_from_dsg()` in `dependencies.py`. This document covers the additional browser-facing auth routes (`/login`, `/logout`, `/profile`) and clio_website frontend changes needed to complete the DSG integration — replacing Google OAuth with the DSG cookie-based flow that neuPrintHTTP + neuPrintExplorer already use.

See `dsg-integration.md` for the existing backend DSG integration design (permission mapping, caching, user management, migration).

## Architecture Decisions

**Cookie domain:** All services (clio-store, clio_website, DSG) are under `*.janelia.org` in production. `AUTH_COOKIE_DOMAIN=.janelia.org` is set in DSG (same as neuprint). The `dsg_token` HttpOnly cookie flows to all services automatically.

**Local development:** Local dev uses the same DSG flow as production. Run with `pixi run dev --certs ../certs` so the browser can attach a `Secure` `dsg_token` cookie over HTTPS — see README.md for the mkcert setup.

## Login Flow (DSG Mode)

```
Browser                    clio-store                  DSG
  |                           |                         |
  |-- click Login ----------->|                         |
  |   GET /login?redirect=    |                         |
  |   https://clio.app/       |                         |
  |                           |                         |
  |<-- 302 Redirect ---------|                         |
  |   to DSG/api/v1/authorize |                         |
  |   ?redirect=clio.app/     |                         |
  |                           |                         |
  |-- follow redirect ------->|------------------------>|
  |                           |    DSG handles OAuth    |
  |<-- Set-Cookie: dsg_token -|-------------------------|
  |   302 redirect to         |                         |
  |   clio.app/               |                         |
  |                           |                         |
  |-- App.js mount            |                         |
  |   GET /profile ---------->|                         |
  |   (cookie: dsg_token)     |-- GET user/cache ------>|
  |                           |<-- user data -----------|
  |<-- { email, roles, ... } -|                         |
  |                           |                         |
  |   POST /v2/server/token ->|                         |
  |   (cookie: dsg_token)     |-- POST create_token --->|
  |                           |<-- bearer_token --------|
  |<-- bearer_token ----------|                         |
  |                           |                         |
  |   store token, render app |                         |
  |   use Bearer token for    |                         |
  |   all subsequent API calls|                         |
```

## Backend Changes (clio-store)

### New file: `services/auth.py`

Auth routes, only registered when `DSG_URL` is set.

| Route | Method | Auth | Purpose |
|-------|--------|------|---------|
| `/login` | GET | None | Redirect to DSG authorize |
| `/profile` | GET | `get_user` | Return user identity + permissions |
| `/logout` | POST | None | Redirect to `{DSG_URL}/api/v1/logout` |

**`GET /login`**
- Query param: `redirect` (required) — frontend URL to return to after DSG auth
- Returns 302 to `{DSG_URL}/api/v1/authorize?redirect={encode(redirect)}`
- DSG handles OAuth, sets `dsg_token` cookie (domain `.janelia.org`), redirects back to `redirect`

**`GET /profile`**
- Uses `Depends(get_user)` for authentication (`dsg_token` cookie sent automatically via `credentials: 'include'`)
- Returns: `{ email, name, global_roles, datasets, groups }`
- This is the DSG-facing equivalent of `GET /v2/roles`

**`POST /logout`**
- Returns 302 to `{DSG_URL}/api/v1/logout`
- No auth required (DSG handles cookie clearing)

### Modify: `main.py`

Mount auth router at top level (not under `/v2/`), without router-level `Depends(get_user)`:
```python
if DSG_URL:
    from services import auth
    app.include_router(auth.router, prefix=f"{URL_PREFIX}")
```

### Modify: `dependencies.py` — CORS credentials support

For `credentials: 'include'` to work cross-origin, update both `preflight_handler` and `add_CORS_header`:
- Echo the specific request `Origin` header (not wildcard `*`)
- Add `Access-Control-Allow-Credentials: true`

## Frontend Changes (clio_website)

### `src/actions/user.js` — DSG auth actions

- `checkDsgAuth(backendBaseUrl)` — called on app mount:
  - `GET {backendBaseUrl}/profile` with `credentials: 'include'` (sends `dsg_token` cookie)
  - If 200: user is logged in → dispatch login, fetch Bearer token from `/v2/server/token`
  - If 401: no valid DSG cookie → return false (fall back to Google OAuth)
  - If 404: backend doesn't have DSG routes → return false

- `dsgLogin(backendBaseUrl)` — `window.location = '{backendBaseUrl}/login?redirect={currentUrl}'`

- `dsgLogout(backendBaseUrl)` — POST `/logout` with `credentials: 'include'`, clear localStorage

### `src/App.js` — DSG-first auth initialization

```
On mount:
  1. Try DSG: call checkDsgAuth(backendBaseUrl)
     → if 200: user is logged in, fetch Bearer token, done
  2. If DSG returns false (401 or 404):
     → fall back to existing localStorage Google OAuth path
  3. Set window.neurohub with the token (works either way)
```

### `src/UnauthenticatedApp.jsx` — conditional login

- If DSG available (Redux flag): show "Login" button → `dsgLogin()`
- If not: show existing `<GoogleSignIn />` component

### `src/GoogleSignIn.jsx` — auth-method-aware logout

- Read `authMethod` from Redux
- If `'dsg'`: logout calls `dsgLogout()` (POST to `/logout`)
- If `'google'`: existing Google logout

### `src/Settings.jsx` — guard Google-specific UI

- Hide Google ID token section when `authMethod === 'dsg'`
- ClioStore/DVID token section stays (already DSG-aware)

## What Does NOT Change

All files using `Authorization: Bearer ${user.token}` for API calls work unchanged — the token is a DSG token instead of a FlyEM JWT, but the usage pattern is identical:
- Annotation components, Atlas, ImageSearch, NeuPrint, Connections
- WorkSpaces, AuthTest
- `window.neurohub` integration for neuroglancer

## Implementation Order

**Backend (clio-store):**
1. `services/auth.py` — new auth routes
2. `main.py` — wire auth router
3. `dependencies.py` — CORS credentials

**Frontend (clio_website):**
4. `reducers/constants.js` + `reducers/user.js` — add `authMethod` state
5. `actions/user.js` — add DSG actions
6. `App.js` — DSG-first init
7. `UnauthenticatedApp.jsx` — conditional login button
8. `GoogleSignIn.jsx` — auth-method-aware logout
9. `Settings.jsx` — guard Google displays
