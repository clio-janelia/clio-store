from fastapi import APIRouter, Depends, HTTPException
import aiohttp
from pydantic import BaseModel

from config import *
from dependencies import app, get_user, get_dataset, User

router = APIRouter()

# Lazily created persistent session (aiohttp requires a running event loop)
_client_session = None

def _get_session():
    global _client_session
    if _client_session is None or _client_session.closed:
        _client_session = aiohttp.ClientSession()
    return _client_session

async def _cleanup():
    if _client_session is not None:
        await _client_session.close()

app.router.on_shutdown.append(_cleanup)

class NeuprintRequest(BaseModel):
    """Request object for custom neuPrint requests."""
    cypher: str
    dataset: str

@router.post('/{dataset}')
@router.post('/{dataset}/', include_in_schema=False)
async def post_neuprint_custom(dataset: str, payload: NeuprintRequest, user: User = Depends(get_user)):
    if not user.has_role("clio_general", dataset):
        raise HTTPException(status_code=403, detail="user doesn't have authorization for this dataset")

    cur_dataset = get_dataset(dataset)
    if cur_dataset.neuprintHTTP:
        neuprint_server = cur_dataset.neuprintHTTP.server
    else:
        raise HTTPException(status_code=400, detail=f"dataset {dataset} has no assigned neuprint server")

    # Forward the user's DSG token to neuPrintHTTP, which authenticates via DatasetGateway.
    if not user.token:
        raise HTTPException(status_code=401, detail="missing user token for neuprint forwarding")
    headers = {"Authorization": f"Bearer {user.token}", "content-type": "application/json"}

    try:
        async with _get_session().post(f'https://{neuprint_server}/api/custom/custom', data=payload.json(), headers=headers) as resp:
            response = await resp.json()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in neuprint request for dataset {dataset}")

    return response
