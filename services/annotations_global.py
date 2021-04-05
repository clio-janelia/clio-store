import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from enum import Enum
from typing import Dict, List, Any, Set, Union, Optional
from pydantic import BaseModel, ValidationError, root_validator

from config import *
from dependencies import get_membership, get_user, User
from stores import firestore

router = APIRouter()

def write_annotation(version, collection, data, user: User):
    data["_version"] = version
    data["_timestamp"] = time.time()
    data["_user"] = user.email
    try:
        collection.add(data)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in writing annotation to dataset {dataset}: {e}\n{data}")

@router.get('/{dataset}/{annotation_type}/{id}', response_model=Union[List, dict])
@router.get('/{dataset}/{annotation_type}/{id}/', response_model=Union[List, dict], include_in_schema=False)
def get_annotations(dataset: str, annotation_type: str, id: str, version: str = "", changes: bool = False, id_field: str = "bodyid", user: User = Depends(get_user)):
    """ Returns the neuron annotation associated with the given id.
        
    Query strings:
        version (str): If supplied, annotations are for the given dataset version.

        changes (bool): If True, returns list of changes to this annotation across all versions.

        id_field (str): The id field name (default: "bodyid")

    Returns:
        A JSON list (if changes requested) or JSON object if not.
    """
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read annotations on dataset {dataset}")

    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS_GLOBAL, annotation_type, dataset])
        query_ref = collection.where(id_field, '==', id)
        results = query_ref.get()

    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving annotations for dataset {dataset}: {e}")


@router.put('/{dataset}/{annotation_type}')
@router.post('/{dataset}/{annotation_type}')
@router.put('/{dataset}/{annotation_type}/', include_in_schema=False)
@router.post('/{dataset}/{annotation_type}/', include_in_schema=False)
async def post_annotations(dataset: str, annotation_type: str, payload: Union[dict, List[dict]], version: str = "", user: User = Depends(get_user)):
    """ Add either a single annotation object or a list of objects. All must be all in the 
        same dataset version.
    """
    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS_GLOBAL, annotation_type, dataset])
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in getting annotations collection for dataset {dataset}: {e}")

    if isinstance(payload, dict):
        write_annotation(version, collection, payload, user)
    else:
        num = 0
        for annotation in payload:
            write_annotation(version, collection, annotation, user)
            num += 1
            if num % 100 == 0:
                print(f"Wrote {num} {annotation_type} annotations to dataset {dataset}...")
