import json
import time

from config import *

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, AnyStr

from dependencies import get_user, User
from stores import firestore

router = APIRouter(
    prefix="/kv"
)

@router.get('/{dataset}/{scope}/{key}')
@router.get('/{dataset}/{scope}/{key}/', include_in_schema=False)
async def get_kv(dataset:str, scope: str, key: str, user: User = Depends(get_user)):
    try:
        collection = firestore.get_collection([CLIO_KEYVALUE, user.email, scope])
        value = collection.document(key).get()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving key-value for key {key} in dataset {dataset}, scope {scope}")
    if not value.exists:
        raise HTTPException(status_code=404, detail=f"no key-value found for key {key} in dataset {dataset}, scope {scope}")
    return value.to_dict()

@router.get('/{dataset}/{scope}')
@router.get('/{dataset}/{scope}/', include_in_schema=False)
async def get_kv_all(dataset:str, scope: str, user: User = Depends(get_user)) -> dict:
    try:
        collection = firestore.get_collection([CLIO_KEYVALUE, user.email, scope])
        kvs = collection.get()
        kvs_out = {}
        for kv in kvs:
            value = kv.to_dict()
            kvs_out[kv.id] = value
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving key-values for dataset {dataset}, scope {scope}")
    return kvs_out

@router.post('/{dataset}/{scope}/{key}')
@router.post('/{dataset}/{scope}/{key}/', include_in_schema=False)
async def post_kv(dataset: str, scope: str, key: str, payload: dict, user: User = Depends(get_user)):
    try:        
        payload["timestamp"] = time.time()
        collection = firestore.get_collection([CLIO_KEYVALUE, user.email, scope])
        collection.document(key).set(payload)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error posting key-value for key {key}, dataset {dataset}, scope {scope}: {e}")

@router.delete('/{dataset}/{scope}/{key}')
@router.delete('/{dataset}/{scope}/{key}/', include_in_schema=False)
async def delete_kv(dataset:str, scope: str, key: str, user: User = Depends(get_user)):
    try:
        collection = firestore.get_collection([CLIO_KEYVALUE, user.email, scope])
        value = collection.document(key).delete()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in deleting key-value for key {key} in dataset {dataset}, scope {scope}")
