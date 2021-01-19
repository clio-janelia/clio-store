import time

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Optional

from config import *
from dependencies import get_user,users, User, CORSHandler
from stores import firestore

router = APIRouter(route_class=CORSHandler)

@router.get('')
@router.get('/', include_in_schema=False)
async def get_users(user: User = Depends(get_user)) -> Dict[str, User]:
    if not user.is_admin():
        raise HTTPException(status_code=401, detail="user lacks permission for /users endpoint")
    try:
        return users.refresh_cache()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving users: {e}")

@router.post('')
@router.post('/', include_in_schema=False)
async def post_users(users: Dict[str, User], user: User = Depends(get_user)):
    if not user.is_admin():
        raise HTTPException(status_code=401, detail="user lacks permission for /users endpoint")
    try:
        collection = firestore.get_collection([CLIO_USERS])
        for email, user in users.items():
            jsondata = user.dict(exclude={'email'})
            collection.document(email).set(jsondata)
            users.cache_user(user)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in posting users: {e}")

@router.delete('')
@router.delete('/', include_in_schema=False)
async def delete_users(deleted_emails: List, user: User = Depends(get_user)):
    if not user.is_admin():
        raise HTTPException(status_code=401, detail="user lacks permission for /users endpoint")
    try:
        collection = firestore.get_collection([CLIO_USERS])
        for email in deleted_emails:
            collection.document(email).delete()
            users.uncache_user(email)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in deleting users: {e}")
