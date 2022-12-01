# HTTP API for Connectomics

Clio Store manages logins, datasets, and other resources useful for large electron microscopy
(EM) datasets.  It is a FastAPI-based server that can be deployed using Google Cloud Run.
It uses a number of Google Cloud services for persistance of data: Cloud Storage,
Firestore, BigQuery.  Authentication is through Google OAuth2 and authorization
is built into the system, allowing selective read/write/metadata access to datasets.  

## Installation

Setup Local Python Environment:

We suggest creating a virual environment using [conda](https://conda.io/projects/conda/en/latest/user-guide/install/index.html), [mamba](https://mamba.readthedocs.io/en/latest/installation.html), or pyenv, and installing python
within that environment.

Then:
```
pip install -r requirements.txt
```

### Run the server locally:
```
uvicorn main:app --reload
```

Then check it out: http://localhost:8000/

### Run on Cloud Run:

```
gcloud builds submit --tag gcr.io/[PROJECT_ID]/clio-store
gcloud run deploy --image gcr.io/[PROJECT_ID]/clio-store --platform managed
```

The first `gcloud builds` command will build the docker image and push it to the Google Container Registry. The second `gcloud run` command will deploy the image to Cloud Run. The `--platform managed` flag is required to deploy to Cloud Run on Google Cloud.

## Environment variables 

Configuration of an owner email, storage specifications, and other variables is handled
through environment variables.  These can be set for Cloud Run services through the
[console, command line, or YAML file](https://cloud.google.com/run/docs/configuring/environment-variables#console).

Here is a list of variables:

URL_PREFIX: a prefix to add to the API endpoint URLs, e.g. "/{URL_PREFIX}/v2/annotations"

ALLOWED_ORIGINS: the allowed origins for CORS `Access-Control-Allow-Origin` header. 
Default is the wildcard (*).

OWNER: the email address of a user that automatically gets admin privileges.

SIG_BUCKET: the GCS bucket specifier for the dataset signatures.

TRANSFER_FUNC: the transfer network cloud run location.

TRANSFER_DEST: the transfer network cache location.

NEUPRINT_APPLICATION_CREDENTIALS: credentials to access neuprint

### Used during local testing or use outside of Cloud Run / Cloud Functions

GOOGLE_APPLICATION_CREDENTIALS: set to credentials for app to access GCP services.

TEST_USER: if set to an email, HTTP API will work as if given user was logged in.

## Note on authentication and authorization

The API is authenticated using a Google identification token, which can be retrieved using
a client side oauth2 javascript client (or using gcloud as in the below examples).  Permissions
are setup to be per dataset and global to clio.  The "clio_general" authorization level
enables a user to add private annotations and view the data associated with the dataset.
"clio_write" enables cross-user global write operations.  "admin" is needed for user management.
Datasets can be marked public, which makes them effectively "clio_general" by default.
Specific applications that work within the clio environment
are welcome to define custom roles or granularity at the dataset or global level.

For now, a token is validated on Google for each invocation but future work involves creating
a JWT, which might be necessary for some low-latency use cases.

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

