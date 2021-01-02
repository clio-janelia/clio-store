from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse

from dependencies import get_user
from services import annotations, atlas, datasets, image_query, image_transfer, kv, users

app = FastAPI(dependencies=[Depends(get_user)])
#app = FastAPI()

app.include_router(annotations.router)
app.include_router(atlas.router)
app.include_router(datasets.router)
app.include_router(image_query.router)
app.include_router(image_transfer.router)
#app.include_router(users.router)
#app.include_router(kv.router)

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
