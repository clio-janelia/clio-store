# HTTP API for Connectomics

Clio Store manages logins, datasets, and other resources useful for large electron microscopy
(EM) datasets.  It is a FastAPI-based server that can be deployed using Google Cloud Run.
It uses a number of Google Cloud services for persistance of data: Cloud Storage,
Firestore, BigQuery.  Authentication is through Google OAuth2 and authorization
is built into the system, allowing selective read/write/metadata access to datasets.  

## Installation

Install [pixi](https://pixi.sh), then:

```
pixi install
```

### Configure environment

Runtime configuration lives in `.env` (gitignored). Bootstrap it with:

```
pixi run setup
```

This walks you through the runtime env vars (DSG_URL is required) and
writes `.env`. Re-running prompts again with the current values as defaults.
`pixi run setup --use-env` skips prompts for keys already in `.env` —
useful in CI or when you only want to fill in missing pieces. See
`.env.example` for the full list of variables and their meaning.

### Run the server locally:
```
pixi run dev
```

Then check it out: http://localhost:8080/

`pixi run dev` runs uvicorn with auto-reload over plain HTTP. To run over HTTPS
with HTTP/2 (matching production) and per-request access logging, pass `--certs`
pointing at a directory holding `localhost+2.pem` and `localhost+2-key.pem`:

```
pixi run dev --certs ../certs
```

This switches the launcher to hypercorn with `--access-logfile -`, so every
request is logged to stdout. See [Local HTTPS with mkcert](#local-https-with-mkcert)
for how to generate the certs.

#### Local HTTPS with mkcert

Some flows (cookie-based auth from a browser, testing against a DSG instance
that requires HTTPS origins) need TLS locally. The dev launcher uses
[mkcert](https://github.com/FiloSottile/mkcert) certs by convention.

1. Install mkcert and trust its local CA (one-time):

   ```
   # macOS:   brew install mkcert nss
   # Linux:   see https://github.com/FiloSottile/mkcert#installation
   mkcert -install
   ```

2. Generate a cert covering localhost, the loopback IP, and a Janelia-style hostname:

   ```
   mkdir -p ../certs && cd ../certs
   mkcert localhost 127.0.0.1 clio-dev.janelia.org
   ```

   This produces `localhost+2.pem` and `localhost+2-key.pem` (the `+2` reflects
   the two extra SANs beyond the first name). Keep the directory outside the
   repo so the keys aren't committed.

3. Add the hostname to `/etc/hosts` so browsers resolve it locally:

   ```
   sudo sh -c 'echo "127.0.0.1 clio-dev.janelia.org" >> /etc/hosts'
   ```

4. Start the server with TLS:

   ```
   pixi run dev --certs ../certs
   ```

   Then open `https://clio-dev.janelia.org:8080/` (or `https://localhost:8080/`).
   Cookie-based DSG auth requires the hostname form, since browsers reject
   `Secure` cookies on plain `localhost` over HTTP.

### Deploy to Cloud Run:

```
pixi run deploy
```

The deploy script interactively prompts for GCP project, region, service name, and
environment variables, and saves settings to `.env` for reuse on subsequent deploys.
Use `pixi run deploy --dry-run` to preview the gcloud command without executing.

Note that we explicitly configure the Cloud Run service to use HTTP/2 which removes
limitations in response sizes (only 32 MiB for HTTP/1). Deployed containers will
use hypercorn because of the need for HTTP/2 to avoid Google's limit on response
sizes.

## Environment variables

Local dev reads these from `.env` (managed by `pixi run setup`). On Cloud Run
they're set on the service — `pixi run deploy` injects them via `--set-env-vars`.

**Required**

- `DSG_URL` — DatasetGateway base URL (e.g. `https://dsg.example.org`). All
  authentication and authorization is delegated here.
- `OWNER` — email that automatically receives global `admin` privileges.

**Optional**

- `URL_PREFIX` — prefix added to every API endpoint, e.g. `"/api"` →
  `/api/v2/annotations`.
- `ALLOWED_ORIGINS` — CORS `Access-Control-Allow-Origin` value. `*` (default)
  or a comma-separated list of origins.
- `SIG_BUCKET` — GCS bucket holding image signatures (powers `/v2/signatures`).
- `TRANSFER_FUNC` — Cloud Function URL for `/v2/transfer`.
- `TRANSFER_DEST` — destination cache for image transfers.

**Local-dev only**

- `GOOGLE_APPLICATION_CREDENTIALS` — path to a GCP service-account JSON. Cloud
  Run uses workload identity automatically; not needed there.

## Authentication and authorization

clio-store delegates all authentication and authorization to DatasetGateway.

- Tokens can be passed via `Authorization: Bearer` header, `dsg_token` cookie,
  or `?dsg_token=` query parameter.
- Get a long-lived API token via `POST /v2/server/token` with any valid
  short-lived token — it proxies to DatasetGateway's token creation endpoint.
- User and role management lives entirely in DatasetGateway's web UI; the
  `/v2/users` endpoints return HTTP 501.
- Per-dataset roles map to DatasetGateway permissions: `view` →
  `clio_general` (read + private annotations), `edit` → `clio_write`
  (cross-user write). Global `admin` enables user management.
- Datasets marked `public=true` in Firestore grant `clio_general` access to
  all authenticated users.

Example call:

    % curl -X GET --header "Authorization: Bearer <dsg-token>" https://my-api-endpoint/v2/datasets

See [docs/dsg-integration.md](docs/dsg-integration.md) for the permission mapping details.

## Adding services

New endpoints can be added by (1) creating new modules in /services and then (2) linking
the endpoint router into the main server in /main.py.

## Debugging

You can use IDEs to debug the server locally, complete with stepping through running code
and examining the stack.  See [Fast API's documentation](https://fastapi.tiangolo.com/tutorial/debugging/#run-your-code-with-your-debugger)
on how to debug using Visual Studio Code and Pycharm.  Note that environment variables are
inherited when launching Visual Studio Code, so if there are authentication/authorization
issues, make sure GOOGLE_APPLICATION_CREDENTIALS is set in the shell where your IDE is
launched.

## API

The API can be viewed via web pages in two different formats:
http://localhost:8000/docs
http://localhost:8000/redoc

Each of the endpoint categories is described below.  For each HTTP request, a proper
Authorization header must be included.

    % curl -X GET --header "Authorization: Bearer $(gcloud auth print-identity-token)" http://my-endpoint-path

To simplify the documentation, the above --header term will be omitted from the example curl
commands below.

### datasets

Datasets are stored in a dictionary where the key is a unique dataset name and the value is
the description and location of the dataset.  If the property "public=true", the dataset will have clio_general privileges to the public.

Post datasets (can post multiple, will overwrite pre-existing):
	
	% curl -X  POST -H "Content-Type: application/json" https://my-api-endpoint/datasets -d '{"mb20": { "description": "4nm MB", "location": "gs://"}}'

Get datasets:
	
	% curl -X GET -H "Content-Type: application/json" https://my-api-endpoint/datasets 

Delete datasets:
	
	% curl -X  DELETE -H "Content-Type: application/json" https://my-api-endpoint/datasets -d '["mb20"]'

### annotations (v1)

Annotations are stored in a dictionary where the key is a unique x_y_z string and the value is 
whatever dictionary payload that is provided by the application.  Annotations are unique per 
dataset.  Annotation retrieval returns every annotation, so this is not designed for 10s of 
thousands of annotations.  In the example below, "mb20" is the name of the dataset.

Post annotation (only one at a time, will overwrite pre-existing):
	
	% curl -X  POST -H "Content-Type: application/json" https://my-api-endpoint/annotations/mb20?x=50\&y=30\&z=50 -d '{"foo": "bar"}'

Get annotations:
	
	% curl -X GET -H "Content-Type: application/json" https://my-api-endpoint/annotations/mb20

Delete annotations (only one at a time):

	curl -X  DELETE -H "Content-Type: application/json" https://my-api-endpoint/annotations/mb20?x=50\&y=30\&z=50

### annotations (v2)

The v2 annotations are more strongly typed and handles points, line segments, and spheres.  Please
look at the online API documentation (/docs or /redoc) for the exact usage.

### Saved Searches

Saved searches are stored in a dictionary where the key is a unique x_y_z string and the value is 
whatever dictionary ayload that is provided by the application.  Saved searches are unique per 
dataset.  Saved searches  retrieval returns every search, so this is not designed for 10s of 
thousands of saved searches  In the example below, "mb20" is the name of the dataset.

Post saved search (only one at a time, will overwrite pre-existing):

	% curl -X  POST -H "Content-Type: application/json"  https://my-api-endpoint/savedsearches/mb20?x=50\&y=30\&z=50 -d '{"foo": "bar"}'

Get saved searches:

	% curl -X GET -H "Content-Type: application/json"  https://my-api-endpoint/savedsearches/mb20

Delete saved search (only one at a time):

	curl -X  DELETE -H "Content-Type: application/json"  https://my-api-endpoint/savedsearches/mb20?x=50\&y=30\&z=50

### Atlas

The "atlas" endpoint is very similar to annotations but it meant for special mark-ups which can be used
as a cross-dataset glossary.  Unlike 'annotations', 'atlas' requires the following JSON fields:

	* title
	* user

If the specified dataset is 'all' the annotations are returned across all datasets as a list.  The function
also automatically adds a timestaamp, location, locationref (which is a string form of the location), a unique primary id, a verified status, and dataset field.

The atlas annotations are initially saved as verified=False.  The user only sees their own annotations when searching by dataset.  When all annotations
are requested, the user only sees annotations for the datasets they have clio_general privilege (or public datasets) that are verified or their own annotations.
There is API to allow a user of clio_write privilege to verify an atlas entry.

Note: posting and deleting can only be done by the user that owns the atlas annotation via the interface.

Post atlas annotation (only one at a time, will overwrite pre-existing):
	
	% curl -X  POST -H "Content-Type: application/json" https://my-api-endpoint/atlas/mb20?x=50\&y=30\&z=50 -d '{"title": "weird point", "description": "it is raally strange", "user": "foo@bar"}'
	
	% curl -X  POST -H "Content-Type: application/json" https://my-api-endpoint/atlas/AbG?x=50\&y=30\&z=50 -d '{"title": "weird point", "description": "it is also strange", "user": "foo@bar"}'

Get atlas annotations for a dataset:
	
	% curl -X GET -H "Content-Type: application/json" https://my-api-endpoint/atlas/mb20

Get ALL atlas annotations across datasets:
	
	% curl -X GET -H "Content-Type: application/json" https://my-api-endpoint/atlas/all

Delete atlas annotation (only one at a time):

	curl -X  DELETE -H "Content-Type: application/json" https://my-api-endpoint/atlas/mb20?x=50\&y=30\&z=50

Toggle verification status for a given annotation using the primary id viewable when retrieving annotations (the user must have clio_write privilege for the dataset).

	curl -X POST -H "Content-Type: application/json" https://us-east4-clio-294722.cloudfunctions.net/clio_toplevel/atlas-verify/PRIMARYKEY

### image transfer

This API allows a user to transfer the specified dataset at a given location and model to another dataset, which is created on-the-fly and viewable in neuroglancer.
Note: TRANSFER_FUNC environment variable must be set to the address of the cloud run function.  TRANSFER_DEST
need to be set to a read public cache.

The input json should be like the following

```json
{
	"dataset": "oldalign_VNC",
	"model_name":  "vnc2hemi:synfocal_3000",
	"center": [20135, 27130, 40622]
}
```

	% curl -X POST -H "Content-Type: application/json" https://my-api-endpoint/transfer --data-binary @transfer.json 

This returns a JSON with {"addr": "neuroglancer link"}

### searching image dataset using signatures

If signatures are computed for a dataset, one can query the signature closest to a given point or find a set of locations similar to the signature found at a given point. 

Find signature near a given point:

	% curl -X GET -H "Content-Type: application/json" https://my-api-endpoint/signatures/atlocation/mb20?x=18416\&y=16369\&z=26467

Find matching points for a signature near a given point:

	% curl -X GET -H "Content-Type: application/json" https://my-api-endpoint/signatures/likelocation/mb20?x=18416\&y=16369\&z=26467


### user management

Admins and the owner can retrieve a list of users, update roles and add new users, and delete users.

Clio supports a list of roles at a global level "global_roles" or per dataset under the "datasets" object.
Cli

Add new user(s) or update roles (roles must be a list):
	
	% curl -X POST -H "Content-Type: application/json" https://my-api-endpoint/users -d '{"foobar@gmail.com": {"global_roles": ["admin", "clio_general" ]}, "datasets": {"hemibrain": ["clio_write"]}}'

Remove user(s):
	
	curl -X DELETE -H "Content-Type: application/json" https://my-api-endpoint/users -d '["plaza.stephen"]'

Retrieve users:
	
	% curl -X GET -H "Content-Type: application/json" https://my-api-endpoint/users

### for applications using this for auth

This service can be used to authorize different applications provided that specified roles have been added
to the user's authorization.  If a user is not on any of the auth lists, there will be a blank authorization
object returned. 

Determine the roles granted to: the users:

	$ curl -X GET -H "Content-Type: application/json" https://my-api-endpoint/roles

