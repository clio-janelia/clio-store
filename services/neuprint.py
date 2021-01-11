from config import *
from fastapi import APIRouter, Depends, HTTPException
from dependencies import get_user, User, app
import aiohttp 
from pydantic import BaseModel

# token for accessing neuprint
NEUPRINT_CREDENTIALS = os.environ.get("NEUPRINT_APPLICATION_CREDENTIALS")

# neuprint address (TODO: move configuration to be in the dataset)
NEUPRINT_URL="https://neuprint.janelia.org/api/custom/custom"

router = APIRouter(
    prefix=f"{URL_PREFIX}/neuprint"
)

# create a persistent session for this service
client_session = aiohttp.ClientSession()

# accesses the global app to register a shutdown event
@app.on_event("shutdown")
async def cleanup():
    await client_session.close()

neuprint_headers = {
    "Authorization": f"Bearer {NEUPRINT_CREDENTIALS}"
}

class NeuprintRequest(BaseModel):
    """Request object for custom neuPrint requests.
    """
    cypher: str
    dataset: str

@router.post('/{dataset}')
@router.post('/{dataset}/', include_in_schema=False)
async def post_neuprint_custom(dataset: str, payload: NeuprintRequest, user: User = Depends(get_user)):
    if not user.has_role("clio_general", dataset):
        raise HTTPException(status_code=403, detail="user doesn't have authorization for this dataset")

    try:
        async with client_session.post(NEUPRINT_URL, data=dict(payload), headers=neuprint_headers) as resp:
            response = await resp.json()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in neuprint request for dataset {dataset}")

    return response
