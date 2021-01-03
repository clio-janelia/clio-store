import time

from config import *

from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_user, User
from stores import firestore

router = APIRouter(
    prefix="/annotations"
)

@router.get('/{dataset}')
@router.get('/{dataset}/', include_in_schema=False)
async def get_annotations(dataset: str, user: User = Depends(get_user)):
    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS, "USER", "annotations"])
        annotations = collection.where("email", "==", user.email).where("dataset", "==", dataset).get()
        output = {}
        for annotation in annotations:
            res = annotation.to_dict()
            res["id"] = annotation.id
            output[res["locationkey"]] = res
        return output
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving annotations for dataset {dataset}")
    return

@router.put('/{dataset}')
@router.post('/{dataset}')
@router.put('/{dataset}/', include_in_schema=False)
@router.post('/{dataset}/', include_in_schema=False)
async def post_annotations(dataset: str, x: int, y: int, z: int, payload: dict, user: User = Depends(get_user)):
    try:        
        payload["timestamp"] = time.time()
        payload["dataset"] = dataset
        payload["location"] = [x, y, z]
        payload["locationkey"] = f"{x}_{y}_{z}"
        payload["email"] = user.email
        collection = firestore.get_collection([CLIO_ANNOTATIONS, "USER", "annotations"])
        collection.document().set(payload)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in put annotation ({x},{y},{z}) for dataset {dataset}")

@router.delete('/{dataset}')
@router.delete('/{dataset}/', include_in_schema=False)
async def delete_annotation(dataset: str, x: int, y: int, z: int, user: User = Depends(get_user)):
    try:
        # delete only supported from interface
        # (delete by dataset + user name + xyz)
        collection = firestore.get_collection([CLIO_ANNOTATIONS, "USER", "annotations"])
        match_list = collection.where("email", "==", user.email).where("locationkey", "==", f"{x}_{y}_{z}").where("dataset", "==", dataset).get()
        for match in match_list:
            match.reference.delete()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in deleting annotation ({x},{y},{z}) for dataset {dataset}")
