import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from enum import Enum
from typing import Dict, List, Any
from pydantic import BaseModel, ValidationError, validator

from config import *
from dependencies import public_dataset, get_user, User
from stores import firestore

router = APIRouter()

class Kind(str, Enum):
    point = 'point'
    lineseg = 'lineseg'
    sphere = 'sphere'

class Annotation(BaseModel):
    kind: Kind
    group: str
    pos: List[int]
    prop: Dict[str, Any]

    @validator('pos')
    def pos_correct_size(cls, v, values):
        kind = values['kind']
        if kind == Kind.point and len(v) != 3:
            raise ValidationError('Point must have 3 elements in pos')
        if kind == Kind.lineseg and len(v) != 6:
            raise ValidationError('Line segment must have 6 elements in pos')
        if kind == Kind.sphere and len(v) != 6:
            raise ValidationError('Sphere must have 6 elements in pos')
        return v

    @validator('prop')
    def prop_has_user(cls, v):
        if "user" not in v:
            raise ValidationError('prop must include user entry')
        return v

    def key(self) -> str:
        if self.kind == Kind.point:
            return f"Pt{self.pos[0]}_{self.pos[1]}_{self.pos[2]}"
        elif self.kind == Kind.lineseg:
            return f"Ln{self.pos[0]}_{self.pos[1]}_{self.pos[2]}_{self.pos[3]}_{self.pos[4]}_{self.pos[5]}"
        elif self.kind == Kind.sphere:
            return f"Sp{self.pos[0]}_{self.pos[1]}_{self.pos[2]}_{self.pos[3]}_{self.pos[4]}_{self.pos[5]}"


@router.get('/{dataset}')
@router.get('/{dataset}/', include_in_schema=False)
async def get_annotations(dataset: str, user: User = Depends(get_user)):
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read annotations on dataset {dataset}")
    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS_V2, dataset, user.email])
        annotations_ref = collection.get()
        output = {}
        for annotation_ref in annotations_ref:
            annotation_dict = annotation_ref.to_dict()
            output[annotation_ref.id] = annotation_dict
        return output
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving annotations for dataset {dataset}")
    return

@router.put('/{dataset}')
@router.post('/{dataset}')
@router.put('/{dataset}/', include_in_schema=False)
@router.post('/{dataset}/', include_in_schema=False)
async def post_annotations(dataset: str, annotation: Annotation, user: User = Depends(get_user)):
    user_email = annotation.prop["user"]
    authorized = (user_email == user.email and user.can_write_own(dataset)) or \
                 (user_email != user.email and user.can_write_others(dataset))
    if not authorized:
        raise HTTPException(status_code=401, detail=f"no permission to add annotation for user {user_email} on dataset {dataset}")
    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS_V2, dataset, user_email])
        annotation_json = jsonable_encoder(annotation)
        key = annotation.key()
        collection.document(key).set(annotation_json)
        return {"key": key}
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in put annotation for dataset {dataset}")

@router.delete('/{dataset}/{key}')
@router.delete('/{dataset}/{key}/', include_in_schema=False)
async def delete_annotation(dataset: str, key: str, user_email: str = "", user: User = Depends(get_user)):
    if user_email == "":
        user_email = user.email
    authorized = (user_email == user.email and user.can_write_own(dataset)) or \
                 (user_email != user.email and user.can_write_others(dataset))
    if not authorized:
        raise HTTPException(status_code=401, detail=f"no permission to delete annotation for user {user_email} on dataset {dataset}")
    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS_V2, dataset, user_email])
        collection.document(key).delete()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in deleting annotation with key {key} for dataset {dataset}")
