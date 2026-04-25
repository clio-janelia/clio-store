from fastapi import APIRouter, Depends, HTTPException, Request

from config import *
from dependencies import get_user, datasets, User, _resolve_token

from stores import cache

import httpx

router = APIRouter()

@router.post('/refresh-caches')
@router.post('/refresh-caches/', include_in_schema=False)
async def refresh_caches(user: User = Depends(get_user)):
    """ Refresh caches rather than wait for timer. """
    datasets.refresh_cache()
    cache.refresh_all()


@router.post('/token')
@router.post('/token/', include_in_schema=False)
async def get_token(request: Request, user: User = Depends(get_user)):
    """Return a long-lived token by proxying to DatasetGateway."""
    token = _resolve_token(request, request.headers.get("Authorization", "").removeprefix("Bearer ").strip())
    try:
        resp = httpx.post(
            f"{DSG_URL}/api/v1/create_token",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"DatasetGateway unavailable: {e}")
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()
