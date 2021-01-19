import time

from fastapi import APIRouter, Depends, HTTPException
from typing import Set

from config import *
from dependencies import get_user,users, User, CORSHandler
from stores import firestore

router = APIRouter(route_class=CORSHandler)

@router.get('')
@router.get('/', include_in_schema=False)
async def get_roles(user: User = Depends(get_user)) -> User:
    """ Return global roles for the user associated with the Credentials token. """
    try:
        return user
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving user roles: {e}")