import time

from config import *

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List, Any, AnyStr, Union

from dependencies import get_user, User, public_dataset
from stores import firestore

router = APIRouter(
    prefix=f"{URL_PREFIX}/atlas"
)

# TODO -- figure out how to allow general JSON in class but with
#         some properties required.  This will allow better validation and OpenAPI docs.

# class AtlasPoint(BaseModel):
#     id: str # key

#     title: str
#     description: str
#     user: str

#     timestamp: float
#     dataset: str
#     location: List[int]
#     locationkey: str
#     email: str
#     verified: bool

# class AtlasPayload(BaseModel):
#     title: str
#     description: str
#     payload: Optional[dict]


#TODO
@router.get('/{dataset}', response_model=Union[Dict, List])
@router.get('/{dataset}/', include_in_schema=False, response_model=Union[Dict, List])
async def get_atlas(dataset: str, user: User = Depends(get_user)):
    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS, "ATLAS", "annotations"])
        if dataset != "all":
            annotations = collection.where("email", "==", user.email).where("dataset", "==", dataset).get()
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
                if public_dataset(user, res["dataset"]) or user.has_role("clio_general", dataset):
                    if res["verified"] or res["email"] == user.email:
                        output.append(res)
                    elif user.has_role("clio_write", dataset):
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
async def post_atlas(dataset: str, x: int, y: int, z: int, payload: dict, user: User = Depends(get_user)):
    if "title" not in payload or "description" not in payload:
        raise HTTPException(status_code=400, detail=f"POSTed object must include 'title' and 'description' properties")
    try:
        payload["timestamp"] = time.time()
        payload["dataset"] = dataset
        payload["location"] = [x, y, z]
        payload["locationkey"] = f"{x}_{y}_{z}"
        payload["email"] = user.email
        payload["verified"] = False
        collection = firestore.get_collection([CLIO_ANNOTATIONS, "ATLAS", "annotations"])
        collection.document().set(payload)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in put annotation ({x},{y},{z}) for dataset {dataset}")

@router.delete('/{dataset}')
@router.delete('/{dataset}/', include_in_schema=False)
async def delete_atlas(dataset: str, x: int, y: int, z: int, user: User = Depends(get_user)):
    try:
        # delete only supported from interface
        # (delete by dataset + user name + xyz)
        collection = firestore.get_collection([CLIO_ANNOTATIONS, "ATLAS", "annotations"])
        match_list = collection.where("email", "==", user.email).where("locationkey", "==", f"{x}_{y}_{z}").where("dataset", "==", dataset).get()
        for match in match_list:
            match.reference.delete()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in deleting annotation ({x},{y},{z}) for dataset {dataset}")
