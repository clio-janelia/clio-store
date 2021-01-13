import time

from config import *

from fastapi import APIRouter, Depends, HTTPException
from typing import Set

from dependencies import get_user,users, User
from stores import firestore

router = APIRouter()

@router.get('')
@router.get('/', include_in_schema=False)
async def get_roles(user: User = Depends(get_user)) -> Set[str]:
    """ Return global roles for the user associated with the Credentials token. """
    try:
        return user.global_roles
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving user roles: {e}")