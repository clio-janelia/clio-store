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
