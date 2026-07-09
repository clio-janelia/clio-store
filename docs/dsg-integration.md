# Clio (clio-store) integration with DatasetGateway

clio-store delegates authentication and authorization to DatasetGateway (DSG)
when `DSG_URL` is configured. Firestore remains the source of Clio dataset
metadata, including every exact Firestore id and its local `public` flag.

## Native authorization adapter

For a new or expired token, clio-store resolves the token from the Bearer
header, `dsg_token` cookie, or `dsg_token` query parameter (in that order),
then performs two native DSG calls:

1. `GET {DSG_URL}/api/dsg/v1/user` obtains the identity, admin flag, and
   groups. Dedicated service-account identities have no email and are rejected
   with 401 because Clio annotation ownership is email-based.
2. `POST {DSG_URL}/api/dsg/v1/authorize` submits one `service: "clio"` batch
   for every id in the Firestore `DatasetCache`.

Each Firestore id is the Clio authorization decision unit. An id with a
nonempty first-colon split is sent as DSG `name` plus `version`; for example,
`fish2:v0.6` becomes `name=fish2`, `version=v0.6`. The remaining text after
the first colon is the version, so `a:b:c` uses version `b:c`. Degenerate
values such as `a:` and `:v1` are sent unsplit as the name. No branch is sent.

The adapter verifies that each response entry echoes the submitted `(name,
version)` at the same index. A length or correlation mismatch fails the whole
refresh rather than associating a decision with the wrong Firestore id.

Native DSG roles map to Clio's preserved `User` interface:

| Native role | Clio role |
|---|---|
| `view` | `clio_general` |
| `edit` | `clio_write` |
| `admin` | `dataset_admin` |
| `manage` | no additional role (it already covers edit and view) |
| another role, such as `annotation_editor` | preserved verbatim |

`allow` decisions populate both normal and ignore-TOS dataset roles.
`tos_required` decisions populate only ignore-TOS roles, so the dataset remains
listed but data routes deny it until acceptance. A decision's `tos_url` is
never cached. `deny` and `service_eval` produce no dataset role. DSG admins
(and the configured `OWNER`) short-circuit every Clio authorization method,
including for a Firestore dataset that DSG does not recognize.

## Caching and acceptance refresh

`_dsg_user_cache` remains token-keyed with a 600-second TTL. The cached `User`
records pending-TOS visibility versus access; this is intentional. A browser
return from TOS reloads the app, and `/profile` always force-refreshes identity
and decisions, then evicts other cached tokens for that email. The next data
request with a sibling long-lived token consequently re-reads DSG immediately.
Data-route denial retains its one `refresh_user()` retry. Acceptance performed
elsewhere is TTL-bounded like any other grant change.

`_dsg_group_members_cache` is also 600 seconds. Annotation visibility queries
use `GET {DSG_URL}/api/dsg/v1/groups/{name}/members`; non-admin users may ask
only about groups included in their native identity.

## Browser routes

The top-level browser routes are mounted only when `DSG_URL` is configured:

| Route | Purpose |
|---|---|
| `GET /login?redirect=...` | 302 to the unchanged DSG auth endpoint with only `redirect`; obsolete `dataset` and `service` query parameters are ignored. |
| `GET /profile` | Cookie-authenticated identity response: `{email, name, picture, global_roles, datasets, groups, dsg_url}`. It is force-fresh. |
| `GET /dataset-access?dataset=...&redirect=...` | Cookie-authenticated, stateless, force-fresh decision for a selected Firestore dataset. |
| `GET` or `POST /logout` | Best-effort DSG logout, local cookie clearing, and redirect. |

`/dataset-access` first reads the native identity. An admin receives access
without an authorization call. Other users receive one authorize entry with
the browser `redirect` sent as DSG's `return_url`. The result contains the
original Firestore `dataset`, booleans `access` and `tos_required`, mapped
roles, and only (when required) the opaque DSG `tos_url`. The endpoint does
not read or write the `User` cache and never returns canonical DSG identifiers.

The unchanged authentication endpoints are DSG authorize for browser login,
long-lived-token proxying at `/v2/server/token`, and DSG logout.

## Local public flag

Firestore's dataset `public: true` flag is still an independent OR-source of
read access, write-own access, and dataset-list visibility. It stays in
Firestore and is refreshed by `DatasetCache`; native DSG `allow` is an
additional access source, not a replacement for local public metadata.

## Rollout

Before deployment, register service `clio` in DSG with linear version
evaluation and register every served Firestore id: bare ids need a matching
dataset or name alias, while colon ids need the dataset, version anchor, and
any necessary version alias. Deploy clio-store (Cloud Run) before
clio_website (the clio-dev bucket). Validate an admin and a granted non-admin
can list and read `fish2:v0.6`, a DSG-public dataset is visible without a
grant, a pending-TOS selection follows its opaque URL and opens after return,
and annotation group visibility remains intact.

## Configuration

| Environment variable | Required | Description |
|---|---|---|
| `DSG_URL` | Yes | DatasetGateway base URL. |
| `OWNER` | Yes | Email granted Clio's global admin short-circuit. |
| `AUTH_COOKIE_DOMAIN` | Recommended on DSG | Shared `dsg_token` cookie domain, such as `.janelia.org`. |
