import time

from config import *

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, AnyStr

from dependencies import get_user, User
from stores import firestore

router = APIRouter(
    prefix=f"{URL_PREFIX}/savedsearches"
)

# TODO -- Create pydantic response model so shows up in OpenAPI docs. 
@router.get('/{dataset}')
@router.get('/{dataset}/', include_in_schema=False)
async def get_searches(dataset: str, user: User = Depends(get_user)):
    try:
        collection = firestore.get_collection([CLIO_SAVEDSEARCHES, "USER", "searches"])
        searches = collection.where("email", "==", user.email).where("dataset", "==", dataset).get()
        output = {}
        for search in searches:
            res = search.to_dict()
            res["id"] = search.id
            output[res["locationkey"]] = res
        return output
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in getting saved searches for dataset {dataset}")

@router.put('/{dataset}')
@router.post('/{dataset}')
@router.put('/{dataset}/', include_in_schema=False)
@router.post('/{dataset}/', include_in_schema=False)
async def searches(dataset: str, x: int, y: int, z: int, payload: dict, user: User = Depends(get_user)):
    try:
        payload["timestamp"] = time.time()
        payload["dataset"] = dataset
        payload["location"] = [x, y, z]
        payload["locationkey"] = f"{x}_{y}_{z}"
        payload["email"] = user.email
        collection = firestore.get_collection([CLIO_SAVEDSEARCHES, "USER", "searches"])
        collection.document().set(payload)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in put annotation ({x},{y},{z}) for dataset {dataset}")

@router.delete('/{dataset}')
@router.delete('/{dataset}/', include_in_schema=False)
async def delete_searches(dataset: str, x: int, y: int, z: int, user: User = Depends(get_user)):
    try:
        # delete only supported from interface
        # (delete by dataset + user name + xyz)
        collection = firestore.get_collection([CLIO_SAVEDSEARCHES, "USER", "searches"])
        match_list = collection.where("email", "==", user.email).where("locationkey", "==", f"{x}_{y}_{z}").where("dataset", "==", dataset).get()
        for match in match_list:
            match.reference.delete()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in deleting saved searches for dataset {dataset}")
