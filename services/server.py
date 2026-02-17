import sys
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Set

from config import *
from dependencies import get_user, users, datasets, User, _resolve_token

from stores import cache

import httpx
import jwt

router = APIRouter()

_SECS_IN_WEEK = 60 * 60 * 24 * 7
_TOKEN_DURATION = 3 * _SECS_IN_WEEK

@router.post('/refresh-caches')
@router.post('/refresh-caches/', include_in_schema=False)
async def refresh_caches(user: User = Depends(get_user)):
    """ Refresh caches rather than wait for timer. """
    datasets.refresh_cache()
    if users is not None:
        users.refresh_cache()
    cache.refresh_all()


@router.post('/token')
@router.post('/token/', include_in_schema=False)
async def get_token(request: Request, user: User = Depends(get_user)):
    """ Return a long-lived token. When DSG_URL is set, proxies to DatasetGate. """
    if DSG_URL:
        token = _resolve_token(request, request.headers.get("Authorization", "").removeprefix("Bearer ").strip())
        try:
            resp = httpx.post(
                f"{DSG_URL}/api/v1/create_token",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"DatasetGate unavailable: {e}")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()

    if FLYEM_SECRET is None:
        raise HTTPException(status_code=400, detail=f"Can't generate FlyEM token because FLYEM_SECRET not specified for this server")
    cur_time = int(time.time())
    exp_time = cur_time + _TOKEN_DURATION
    flyem_jwt = {
        'email': user.email,
        'iat': cur_time,
        'exp': exp_time,
        'iss': 'flyem-clio-store'
    }
    # don't use update since not sure what the mapping token return constitutes
    safe_fields = set(["hd", "email_verified", "name", "picture", "given_name", "family_name", "locale"])
    if user.google_idinfo:
        for key, value in user.google_idinfo.items():
            if key not in flyem_jwt and key in safe_fields:
                flyem_jwt[key] = value
    try:
        token = jwt.encode(flyem_jwt, FLYEM_SECRET, algorithm='HS256')
    except:
        e = sys.exc_info()[0]
        raise HTTPException(status_code=400, detail=f"Can't generate FlyEM token: {e}")
    return token
    