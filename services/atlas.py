import time

from config import *

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List, Any, AnyStr, Union

from dependencies import get_user, User, public_dataset
from stores import firestore

router = APIRouter()

@router.get('/{dataset}', response_model=Union[Dict, List])
@router.get('/{dataset}/', include_in_schema=False, response_model=Union[Dict, List])
async def get_atlas(dataset: str, user: User = Depends(get_user)):
    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS, "ATLAS", "annotations"])
        if dataset != "all":
            if not user.can_read(dataset):
                raise HTTPException(status_code=401, detail=f"no permission to read annotations in dataset {dataset}")
            annotations = collection.where("user", "==", user.email).where("dataset", "==", dataset).get()
            output = {}
            for annotation in annotations:
                res = annotation.to_dict()
                res["id"] = annotation.id
                output[res["locationkey"]] = res
            return output
        else:
            annotations = collection.get()
            output = []
            for annotation in annotations:
                res = annotation.to_dict()
                res["id"] = annotation.id
                annot_dataset = res.get("dataset", "")
                if user.can_read(annot_dataset):
                    if res["verified"] or res["user"] == user.email:
                        output.append(res)
                    elif user.can_write_others(annot_dataset):
                        output.append(res)
            return output

    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving atlas for dataset {dataset}")
    return

@router.put('/{dataset}')
@router.post('/{dataset}')
@router.put('/{dataset}/', include_in_schema=False)
@router.post('/{dataset}/', include_in_schema=False)
async def post_atlas(dataset: str, x: int, y: int, z: int, payload: dict, user: User = Depends(get_user)) -> dict:
    if "title" not in payload or "description" not in payload or "user" not in payload:
        raise HTTPException(status_code=400, detail=f"POSTed object must include 'title', 'description', and 'user' properties")
    if not user.can_write_own(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to write annotations in dataset {dataset}")
    if payload["user"] != user.email and not user.can_write_others(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to write others' annotations in dataset {dataset}")
    try:
        payload["timestamp"] = time.time()
        payload["dataset"] = dataset
        payload["location"] = [x, y, z]
        payload["locationkey"] = f"{x}_{y}_{z}"
        if "verified" not in payload:
            payload["verified"] = False
        collection = firestore.get_collection([CLIO_ANNOTATIONS, "ATLAS", "annotations"])
        collection.document().set(payload)
        return payload
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in put annotation ({x},{y},{z}) for dataset {dataset}")

@router.delete('/{dataset}')
@router.delete('/{dataset}/', include_in_schema=False)
async def delete_atlas(dataset: str, x: int, y: int, z: int, user: User = Depends(get_user)):
    if not user.can_write_own(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to delete annotations in dataset {dataset}")
    try:
        # delete only supported from interface
        # (delete by dataset + user name + xyz)
        collection = firestore.get_collection([CLIO_ANNOTATIONS, "ATLAS", "annotations"])
        match_list = collection.where("user", "==", user.email).where("locationkey", "==", f"{x}_{y}_{z}").where("dataset", "==", dataset).get()
        for match in match_list:
            match.reference.delete()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in deleting annotation ({x},{y},{z}) for dataset {dataset}")
