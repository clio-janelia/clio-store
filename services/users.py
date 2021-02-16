import time

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Set, Optional
from pydantic import BaseModel

from config import *
from dependencies import get_user, users, User
from stores import firestore

router = APIRouter()

@router.get('')
@router.get('/', include_in_schema=False)
def get_users(user: User = Depends(get_user)) -> Dict[str, User]:
    if not user.is_admin():
        raise HTTPException(status_code=401, detail="user lacks permission for /users endpoint")
    try:
        return users.refresh_cache()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving users: {e}")

class UserPayload(BaseModel):
    global_roles: Optional[Set[str]] = set()
    datasets: Optional[Dict[str, Set[str]]] = {}
  
@router.post('')
@router.post('/', include_in_schema=False)
def post_users(postdata: Dict[str, UserPayload], user: User = Depends(get_user)):
    if not user.is_admin():
        raise HTTPException(status_code=401, detail="user lacks permission for /users endpoint")
    try:
        collection = firestore.get_collection([CLIO_USERS])
        for email, data in postdata.items():
            user_dict = data.dict()
            collection.document(email).set(user_dict)
            user_dict["email"] = email
            user_with_email = User(**user_dict)
            users.cache_user(user_with_email)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in posting users: {e}")

@router.delete('')
@router.delete('/', include_in_schema=False)
def delete_users(deleted_emails: List, user: User = Depends(get_user)):
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
