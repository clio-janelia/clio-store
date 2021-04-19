import time

from fastapi import APIRouter, Depends, HTTPException
from typing import Set

from config import *
from dependencies import get_user, users, datasets, User
from stores import firestore

router = APIRouter()

@router.post('/refresh-caches')
@router.post('/refresh-caches', include_in_schema=False)
async def refresh_caches(user: User = Depends(get_user)):
    """ Refresh caches rather than wait for timer. """
    datasets.refresh_cache()
    users.refresh_cache()
