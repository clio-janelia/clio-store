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
(and the configured `OWNER`) short-circuit the metadata-oriented Clio role
methods, including for a Firestore dataset that DSG does not recognize.
DVID-backed annotation data calls still use the node-scoped broker described
below; DVID authenticates admins there before minting a capability without an
ordinary alias-backed grant.

## DVID-backed JSON annotations

Every mounted `/v2/json-annotations` route that reads or mutates annotation
data first resolves exactly one DVID target from the Firestore dataset metadata:

- omitted version uses the configured head UUID;
- a `v...` Clio tag uses the stored `tag_to_uuid` mapping;
- a bare partial or full UUID must contain only hexadecimal characters.

clio-store then sends the user's DSG Bearer token to
`POST {dvid}/api/auth/clio/{uuid}` with `permission: "view"` for reads or
`permission: "edit"` for writes. It does not send a caller-supplied branch,
raw `VersionID`, dataset name, or service. DVID uniquely resolves the UUID,
derives the root/branch/raw `VersionID`, authorizes it as `service=clio`, and
returns an opaque, short-lived capability on allow. Ambiguous UUIDs and broker
errors fail closed. The read-only neuronjson `POST /query` uses view scope;
ordinary mutation POSTs require edit.

There is one broker request per incoming Clio request, including a request that
writes a list of annotations. Each corresponding data call carries only:

```text
Authorization: DVID-Capability <opaque>
```

The data call never receives the user's DSG token or `X-DVID-Internal`. A
`tos_required` decision becomes HTTP 403 with only its opaque `tos_url`; deny
also becomes 403, and malformed or unavailable broker responses fail closed.
When `ALLOWED_ORIGINS` is an explicit comma-separated list, the request Origin
is forwarded as `return_url` only if it is an exact member. No return URL is
forwarded for wildcard CORS or an unlisted Origin.

Metadata-only annotation routes (`versions`, `head_tag`, `head_uuid`, and the
tag/UUID lookup routes) do not contact DVID. They enforce a real dataset-grain
read check through the cached `User` interface. Data routes deliberately do not
preempt the broker with that cache check, so a version-only grant can reach its
eligible DVID node. `designated_user` and `u` remain application metadata, but
DVID uses the principal bound into the capability as the security and audit
actor.

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

Deploy the DSG native core first. Ensure the existing `clio` service uses DAG
version evaluation. Retain the Firestore-id aliases used by dataset listing and
`/dataset-access`; additionally register each Clio-backed DVID root UUID as a
name alias and add a `(root UUID, decimal VersionID)` alias for every published
anchor. Inspect metadata listing for the known alphabetic-alias wart even when
direct alias resolution succeeds.

Configure the capability signing secret on every DVID process involved in
broker minting or annotation serving, keep top-level DVID `enforce = "dsg"`,
and deploy DVID before clio-store. Secret TTL, skew, and active/previous-key
rotation are documented in DVID's DSG authorization guide. No Firestore `dvid`
URL, nginx, dual-horizon DNS, or `X-DVID-Internal` change is required; the
legacy internal-header path remains available to other clients but is not
Clio's authorization proof. No clio_website change is required for this slice.

Validate dataset- and version-grain users across same- and cross-branch nodes,
read/edit separation, the read-only query POST, TOS acceptance and retry,
deny/admin behavior, write attribution, tampering, expiry, and cross-node
capability misuse. Also verify metadata-only route denial and inspect aliases.

## Configuration

| Environment variable | Required | Description |
|---|---|---|
| `DSG_URL` | Yes | DatasetGateway base URL. |
| `OWNER` | Yes | Email granted Clio's global admin short-circuit. |
| `AUTH_COOKIE_DOMAIN` | Recommended on DSG | Shared `dsg_token` cookie domain, such as `.janelia.org`. |
