import time

from config import *

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, AnyStr, Optional

from dependencies import get_user, User, CORSHandler
from stores import firestore

router = APIRouter(route_class=CORSHandler)

@router.get('/{dataset}/{scope}/{key}')
@router.get('/{dataset}/{scope}/{key}/', include_in_schema=False)
async def get_kv(dataset:str, scope: str, key: str, timestamp: bool = False, user: User = Depends(get_user)):
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read key-values in dataset {dataset}")
    try:
        collection = firestore.get_collection([CLIO_KEYVALUE, user.email, scope])
        value_ref = collection.document(key).get()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving key-value for key {key} in dataset {dataset}, scope {scope}")
    if not value_ref.exists:
        raise HTTPException(status_code=404, detail=f"no key-value found for key {key} in dataset {dataset}, scope {scope}")
    value = value_ref.to_dict()
    if not timestamp:
        del value["_timestamp"]
    return value

@router.get('/{dataset}/{scope}')
@router.get('/{dataset}/{scope}/', include_in_schema=False)
async def get_kv_all(dataset:str, scope: str, timestamp: bool = False, user: User = Depends(get_user)) -> dict:
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read key-values in dataset {dataset}")
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
        raise HTTPException(status_code=400, detail=f"error in retrieving key-values for dataset {dataset}, scope {scope}")
    return kvs_out

@router.post('/{dataset}/{scope}/{key}')
@router.post('/{dataset}/{scope}/{key}/', include_in_schema=False)
async def post_kv(dataset: str, scope: str, key: str, payload: dict, user: User = Depends(get_user)):
    if not user.can_write_own(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to write key-values in dataset {dataset}")
    try:        
        payload["_timestamp"] = time.time()
        collection = firestore.get_collection([CLIO_KEYVALUE, user.email, scope])
        collection.document(key).set(payload)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error posting key-value for key {key}, dataset {dataset}, scope {scope}: {e}")

@router.delete('/{dataset}/{scope}/{key}')
@router.delete('/{dataset}/{scope}/{key}/', include_in_schema=False)
async def delete_kv(dataset:str, scope: str, key: str, user: User = Depends(get_user)):
    if not user.can_write_own(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to delete key-values in dataset {dataset}")
    try:
        collection = firestore.get_collection([CLIO_KEYVALUE, user.email, scope])
        collection.document(key).delete()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in deleting key-value for key {key} in dataset {dataset}, scope {scope}")
