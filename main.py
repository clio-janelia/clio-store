from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from config import URL_PREFIX
from dependencies import get_user
from services import annotations_v2, annotations_v1, atlas, datasets, image_query, image_transfer, kv, savedsearches

app = FastAPI()

# Wire in the API endpoints
# require user authorization for any of the actual data API calls
# versions: "clio_toplevel" is v1, other versions are explicitly "v2", etc.
app.include_router(annotations_v1.router, prefix=f"{URL_PREFIX}/clio_toplevel/annotations", dependencies=[Depends(get_user)])
app.include_router(atlas.router, prefix=f"{URL_PREFIX}/clio_toplevel/atlas", dependencies=[Depends(get_user)])
app.include_router(datasets.router, prefix=f"{URL_PREFIX}/clio_toplevel/datasets", dependencies=[Depends(get_user)])
app.include_router(image_query.router, prefix=f"{URL_PREFIX}/clio_toplevel/signatures", dependencies=[Depends(get_user)])
app.include_router(image_transfer.router, prefix=f"{URL_PREFIX}/clio_toplevel/transfer", dependencies=[Depends(get_user)])
app.include_router(kv.router, prefix=f"{URL_PREFIX}/clio_toplevel/kv", dependencies=[Depends(get_user)])
app.include_router(savedsearches.router, prefix=f"{URL_PREFIX}/clio_toplevel/savedsearches", dependencies=[Depends(get_user)])
#app.include_router(users.router)

app.include_router(annotations_v2.router, prefix=f"{URL_PREFIX}/v2/annotations", dependencies=[Depends(get_user)])
app.include_router(atlas.router, prefix=f"{URL_PREFIX}/v2/atlas", dependencies=[Depends(get_user)])
app.include_router(datasets.router, prefix=f"{URL_PREFIX}/v2/datasets", dependencies=[Depends(get_user)])
app.include_router(image_query.router, prefix=f"{URL_PREFIX}/v2/signatures", dependencies=[Depends(get_user)])
app.include_router(image_transfer.router, prefix=f"{URL_PREFIX}/v2/transfer", dependencies=[Depends(get_user)])
app.include_router(kv.router, prefix=f"{URL_PREFIX}/v2/kv", dependencies=[Depends(get_user)])
app.include_router(savedsearches.router, prefix=f"{URL_PREFIX}/v2/savedsearches", dependencies=[Depends(get_user)])

# Handle CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins='*',
    allow_credentials=False, # Can't be True (cookies supported for CORS) if origins=*
    allow_methods=['*'],
    allow_headers=['*'],
)

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

