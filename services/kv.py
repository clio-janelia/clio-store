import time

from config import *

from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_user, User
from stores import firestore

router = APIRouter()

@router.get('/{scope}/{key}')
@router.get('/{scope}/{key}/', include_in_schema=False)
async def get_kv(scope: str, key: str, timestamp: bool = False, user: User = Depends(get_user)):
    """
    Gets the key-value in the given scope with the given key for the authenticated user.  
    If timestamp = True, returns a _timestamp property with the Unix time of key-value persistence.
    """
    try:
        collection = firestore.get_collection([CLIO_KEYVALUE, user.email, scope])
        value_ref = collection.document(key).get()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving key-value for key {key}, scope {scope}")
    if not value_ref.exists:
        raise HTTPException(status_code=404, detail=f"no key-value found for key {key}, scope {scope}")
    value = value_ref.to_dict()
    if not timestamp:
        del value["_timestamp"]
    return value

@router.get('/{scope}')
@router.get('/{scope}/', include_in_schema=False)
async def get_kv_all(scope: str, timestamp: bool = False, user: User = Depends(get_user)) -> dict:
    """
    Gets all key-values in the given scope for the authenticated user.  If timestamp = True, returns
    a _timestamp property with the Unix time of key-value persistence.
    """
    try:
        collection = firestore.get_collection([CLIO_KEYVALUE, user.email, scope])
        kvs = collection.get()
        kvs_out = {}
        for kv_ref in kvs:
            value = kv_ref.to_dict()
            if not timestamp:
                del value["_timestamp"]
            kvs_out[kv_ref.id] = value
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving key-values for scope {scope}")
    return kvs_out

@router.post('/{scope}/{key}')
@router.post('/{scope}/{key}/', include_in_schema=False)
async def post_kv(scope: str, key: str, payload: dict, user: User = Depends(get_user)):
    """Puts a key-value in the given scope for the authenticated user."""
    try:        
        payload["_timestamp"] = time.time()
        collection = firestore.get_collection([CLIO_KEYVALUE, user.email, scope])
        collection.document(key).set(payload)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error posting key-value for key {key}, scope {scope}: {e}")

@router.delete('/{scope}/{key}')
@router.delete('/{scope}/{key}/', include_in_schema=False)
async def delete_kv(scope: str, key: str, user: User = Depends(get_user)):
    """Deletes a key-value with the given key in the given scope for the authenticated user."""
    try:
        collection = firestore.get_collection([CLIO_KEYVALUE, user.email, scope])
        collection.document(key).delete()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in deleting key-value for key {key}, scope {scope}")
