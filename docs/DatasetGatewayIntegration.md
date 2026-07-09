# DatasetGateway browser integration

clio-store and clio_website use the DSG `dsg_token` HttpOnly cookie for browser
authentication. In production the services are under `*.janelia.org`, so DSG
uses `AUTH_COOKIE_DOMAIN=.janelia.org`. Local development needs HTTPS (for
example, `pixi run dev --certs ../certs`) before a browser will send the secure
cookie.

## Login and profile

The website sends the current page as `redirect` to clio-store's top-level
`/login`. clio-store redirects to the DSG authorize endpoint with that one
parameter. Older frontends that still append `dataset` or `service` are
accepted but those values are ignored.

After DSG returns the browser, the website fetches `/profile` with
`credentials: 'include'`. This force-refreshes the cached identity and returns
only:

```json
{
  "email": "...",
  "name": "...",
  "picture": "...",
  "global_roles": [],
  "datasets": {},
  "groups": [],
  "dsg_url": "..."
}
```

`/v2/server/token` continues to proxy DSG's stable long-lived token, and
`/logout` continues to clear the shared cookie after its best-effort DSG call.

## Dataset access and TOS

The frontend does not derive TOS status, canonical dataset names, or a DSG
URL from profile data. It asks clio-store for a fresh decision per selected
Firestore dataset:

```
GET /dataset-access?dataset=<Firestore id>&redirect=<current frontend URL>
```

The endpoint responds with `{dataset, access, tos_required, roles}` and, for a
pending TOS, an opaque `tos_url`. The frontend navigates to that URL directly
on both a user selection and an initial/deep-link selection. It sets a
session-scoped, per-dataset loop guard before navigating. If the same selected
dataset returns pending TOS after that one redirect, the frontend displays the
fallback card rather than looping; a successful access decision or selection
change clears the guard.

The access check is delayed until there is an authenticated user, a loaded
dataset list, and a resolved dataset key. Responses are tied to the selection
that issued them, so a stale response cannot redirect or replace the state of
a newer selection. Failure or an unavailable endpoint fails open: the UI
renders and data routes continue to enforce authorization.

## CORS

Browser requests use `credentials: 'include'`. clio-store reflects an allowed
origin rather than returning a wildcard and sets
`Access-Control-Allow-Credentials: true` for both normal and preflight
responses.

## Deployment order

Deploy clio-store before clio_website. This ensures the website's access calls
reach the native endpoint. During a transition, the new website fails open
against an older store, while an older website may not direct a pending-TOS
user until its data route denies access.
