from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse

from dependencies import get_user
from services import annotations, atlas, datasets, image_query, image_transfer, kv, savedsearches

app = FastAPI()

# require user authorization for any of the actual data API calls
app.include_router(annotations.router, dependencies=[Depends(get_user)])
app.include_router(atlas.router, dependencies=[Depends(get_user)])
app.include_router(datasets.router, dependencies=[Depends(get_user)])
app.include_router(image_query.router, dependencies=[Depends(get_user)])
app.include_router(image_transfer.router, dependencies=[Depends(get_user)])
app.include_router(kv.router, dependencies=[Depends(get_user)])
app.include_router(savedsearches.router, dependencies=[Depends(get_user)])
#app.include_router(users.router)

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
