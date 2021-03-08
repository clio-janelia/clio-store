import time

from config import *

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List, Any, AnyStr, Union

from dependencies import get_user, User, public_dataset
from stores import firestore

router = APIRouter()

@router.get('/{dataset}', response_model=Union[Dict, List])
@router.get('/{dataset}/', include_in_schema=False, response_model=Union[Dict, List])
def get_atlas(dataset: str, user: User = Depends(get_user)):
    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS, "ATLAS", "annotations"])
        if dataset != "all":
            if not user.can_read(dataset):
                raise HTTPException(status_code=401, detail=f"no permission to read annotations")
            annotations = collection.where("user", "==", user.email).where("dataset", "==", dataset).get()
            output = {}
            for annotation in annotations:
                res = annotation.to_dict()
                res["id"] = annotation.id
                output[res["locationkey"]] = res
            return output
        else:
            annotations = collection.stream()
            output = []
            for annotation in annotations:
                res = annotation.to_dict()
                annot_dataset = res.get("dataset", "")
                if user.can_read(annot_dataset):
                    if "verified" not in res:
                        print(f"bad atlas point, adding 'verified': {res}")
                        res["verified"] = False
                        annotation.reference.set(res)
                    res["id"] = annotation.id
                    if res["verified"] or res["user"] == user.email:
                        output.append(res)
                    elif user.can_write_others(annot_dataset):
                        output.append(res)
            return output

    except HTTPException as e:
        raise e
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving atlas for dataset {dataset}: {e}")
    return

@router.put('/{dataset}')
@router.post('/{dataset}')
@router.put('/{dataset}/', include_in_schema=False)
@router.post('/{dataset}/', include_in_schema=False)
def post_atlas(dataset: str, x: int, y: int, z: int, payload: dict, user: User = Depends(get_user)) -> dict:
    if "title" not in payload or "user" not in payload:
        raise HTTPException(status_code=400, detail=f"POSTed object must include 'title' and 'user' properties")
    if not user.can_write_own(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to write annotations in dataset {dataset}")
    if payload["user"] != user.email and not user.can_write_others(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to write others' annotations in dataset {dataset}")
    if "verified" in payload and payload["verified"] and not user.has_role("clio_write", dataset):
        raise HTTPException(status_code=401, detail=f"no permission to set verified status in dataset {dataset}")
    try:
        location_key = f"{x}_{y}_{z}"
        collection = firestore.get_collection([CLIO_ANNOTATIONS, "ATLAS", "annotations"])
        annotations = collection.where("user", "==", payload["user"]) \
                               .where("dataset", "==", dataset) \
                               .where("locationkey", "==", f"{x}_{y}_{z}").get()
        payload["timestamp"] = time.time()
        payload["dataset"] = dataset
        payload["location"] = [x, y, z]
        payload["locationkey"] = location_key
        if len(annotations) == 0:
            new_ref = collection.document()
            payload["id"] = new_ref.id
            if "verified" in payload:
                if payload["verified"] and not user.has_role("clio_write", dataset):
                    raise HTTPException(status_code=401, detail=f"no permission to set verified atlas pt")
            else:
                payload["verified"] = False
            new_ref.set(payload)
        else:
            first = True
            for annotation in annotations:
                if first:
                    current = annotation.to_dict()
                    if "verified" in current and current["verified"] and not user.has_role("clio_write", dataset):
                        raise HTTPException(status_code=401, detail=f"no permission to modify verified atlas pt")
                    payload["id"] = annotation.id
                    annotation.reference.set(payload)
                    first = False
                else:  # shouldn't happen unless legacy bad points.
                    print(f"deleted duplicate atlas annotation point: {annotation}")
                    annotation.reference.delete()
        return payload
    except HTTPException as e:
        raise e
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in put annotation ({x},{y},{z}) for dataset {dataset}: {e}")

@router.delete('/{dataset}')
@router.delete('/{dataset}/', include_in_schema=False)
def delete_atlas(dataset: str, x: int, y: int, z: int, user: User = Depends(get_user)):
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
