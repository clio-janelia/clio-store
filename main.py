from fastapi import Depends
from fastapi.responses import HTMLResponse

from config import URL_PREFIX
from dependencies import get_user, app
from services import annotations_v3, annotations_v2, atlas, datasets, image_query, image_transfer, \
    kv, savedsearches, users, roles, neuprint, subvol_edit, pull_request, server, \
    json_annotations, json_annotations_vnc, volumes    

# Wire in the API endpoints
# require user authorization for any of the actual data API calls
# versions are explicitly "v2", etc, and there is a "test" for ephemeral mods during testing.
app.include_router(annotations_v3.router, prefix=f"{URL_PREFIX}/test/annotations", dependencies=[Depends(get_user)], include_in_schema=False)
app.include_router(atlas.router, prefix=f"{URL_PREFIX}/test/atlas", dependencies=[Depends(get_user)], include_in_schema=False)
app.include_router(neuprint.router, prefix=f"{URL_PREFIX}/test/neuprint", dependencies=[Depends(get_user)], include_in_schema=False)
app.include_router(datasets.router, prefix=f"{URL_PREFIX}/test/datasets", dependencies=[Depends(get_user)], include_in_schema=False)
app.include_router(image_query.router, prefix=f"{URL_PREFIX}/test/signatures", dependencies=[Depends(get_user)], include_in_schema=False)
app.include_router(image_transfer.router, prefix=f"{URL_PREFIX}/test/transfer", dependencies=[Depends(get_user)], include_in_schema=False)
app.include_router(kv.router, prefix=f"{URL_PREFIX}/test/kv", dependencies=[Depends(get_user)], include_in_schema=False)
app.include_router(savedsearches.router, prefix=f"{URL_PREFIX}/test/savedsearches", dependencies=[Depends(get_user)], include_in_schema=False)
app.include_router(users.router, prefix=f"{URL_PREFIX}/test/users", dependencies=[Depends(get_user)], include_in_schema=False)
app.include_router(roles.router, prefix=f"{URL_PREFIX}/test/roles", dependencies=[Depends(get_user)], include_in_schema=False)
app.include_router(pull_request.router, prefix=f"{URL_PREFIX}/test/pull-request", dependencies=[Depends(get_user)], include_in_schema=False)

#app.include_router(json_annotations_vnc.router, prefix=f"{URL_PREFIX}/v2/json-annotations/VNC", dependencies=[Depends(get_user)])
app.include_router(json_annotations.router, prefix=f"{URL_PREFIX}/v2/json-annotations", dependencies=[Depends(get_user)])

app.include_router(annotations_v3.router, prefix=f"{URL_PREFIX}/v2/annotations", dependencies=[Depends(get_user)])
app.include_router(atlas.router, prefix=f"{URL_PREFIX}/v2/atlas", dependencies=[Depends(get_user)])
app.include_router(neuprint.router, prefix=f"{URL_PREFIX}/v2/neuprint", dependencies=[Depends(get_user)])
app.include_router(datasets.router, prefix=f"{URL_PREFIX}/v2/datasets", dependencies=[Depends(get_user)])
app.include_router(image_query.router, prefix=f"{URL_PREFIX}/v2/signatures", dependencies=[Depends(get_user)])
app.include_router(image_transfer.router, prefix=f"{URL_PREFIX}/v2/transfer", dependencies=[Depends(get_user)])
app.include_router(kv.router, prefix=f"{URL_PREFIX}/v2/kv", dependencies=[Depends(get_user)])
app.include_router(savedsearches.router, prefix=f"{URL_PREFIX}/v2/savedsearches", dependencies=[Depends(get_user)])
app.include_router(users.router, prefix=f"{URL_PREFIX}/v2/users", dependencies=[Depends(get_user)])
app.include_router(roles.router, prefix=f"{URL_PREFIX}/v2/roles", dependencies=[Depends(get_user)])
#app.include_router(subvol_edit.router, prefix=f"{URL_PREFIX}/v2/subvol", dependencies=[Depends(get_user)])
app.include_router(pull_request.router, prefix=f"{URL_PREFIX}/v2/pull-request", dependencies=[Depends(get_user)])
app.include_router(server.router, prefix=f"{URL_PREFIX}/v2/server", dependencies=[Depends(get_user)])
app.include_router(volumes.router, prefix=f"{URL_PREFIX}/v2/volumes")

# allow unauthenticated to access root documentation
@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <html>
    <h1>Clio Store</h1>
    <p>
Clio Store manages logins, datasets, and other resources useful for large electron microscopy
(EM) datasets.  It is a FastAPI-based server that can be deployed using Google Cloud Run.
It uses a number of Google Cloud services for persistance of data: Cloud Storage,
Firestore, BigQuery.  Authentication is through Google OAuth2 and authorization
is built into the system, allowing selective read/write/metadata access to datasets.
    </p>
    <h3>API Documentation</h3>
    <ul>
        <li><a href="/docs">Swagger-style API Documentation</a></li>
        <li><a href="/redoc">ReDoc-style API Documentation</a></li>
    </ul>
    </html>
    """

