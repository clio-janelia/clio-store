from fastapi import Depends
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from dependencies import get_user, app
from services import annotations, atlas, datasets, image_query, image_transfer, kv, savedsearches, neuprint

# Wire in the API endpoints
# require user authorization for any of the actual data API calls
app.include_router(annotations.router, dependencies=[Depends(get_user)])
app.include_router(neuprint.router, dependencies=[Depends(get_user)])
app.include_router(atlas.router, dependencies=[Depends(get_user)])
app.include_router(datasets.router, dependencies=[Depends(get_user)])
app.include_router(image_query.router, dependencies=[Depends(get_user)])
app.include_router(image_transfer.router, dependencies=[Depends(get_user)])
app.include_router(kv.router, dependencies=[Depends(get_user)])
app.include_router(savedsearches.router, dependencies=[Depends(get_user)])
#app.include_router(users.router)

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
    <h3>API Documentation</h3>
    <ul>
        <li><a href="/redoc">Newer API Documentation format</a></li>
        <li><a href="/docs">Older API Documentation format</a></li>
    </ul>
    </html>
    """

