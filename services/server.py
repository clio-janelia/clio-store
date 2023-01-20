import sys
import time

from fastapi import APIRouter, Depends, HTTPException
from typing import Set

from config import *
from dependencies import get_user, users, datasets, User
from stores import cache

import jwt

router = APIRouter()

_SECS_IN_WEEK = 60 * 60 * 24 * 7
_TOKEN_DURATION = 3 * _SECS_IN_WEEK

@router.post('/refresh-caches')
@router.post('/refresh-caches/', include_in_schema=False)
async def refresh_caches(user: User = Depends(get_user)):
    """ Refresh caches rather than wait for timer. """
    datasets.refresh_cache()
    users.refresh_cache()
    cache.refresh_all()


@router.post('/token')
@router.post('/token/', include_in_schema=False)
async def get_token(user: User = Depends(get_user)):
    """ Return a long-lived FlyEM token """
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
    