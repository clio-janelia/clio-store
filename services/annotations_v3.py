import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from enum import Enum
from typing import Dict, List, Any, Set, Union, Optional
from pydantic import BaseModel, ValidationError, root_validator

from config import *
from dependencies import group_members, get_user, User
from stores import firestore

router = APIRouter()

class Kind(str, Enum):
    point = 'point'
    lineseg = 'lineseg'
    sphere = 'sphere'

class Annotation(BaseModel):
    kind: Kind
    pos: List[int]
    prop: Optional[Dict[str, Any]]
    tags: Optional[List[str]]
    user: Optional[str]
    title: Optional[str]
    description: Optional[str]

    @root_validator
    def pos_correct_size(cls, v):
        if 'kind' not in v:
            raise ValidationError('"kind" property must exist')
        kind = v['kind']
        if 'pos' not in v:
            raise ValidationError('"pos" integer array must be present')
        pos_length = len(v['pos'])
        if kind == Kind.point and pos_length != 3:
            raise ValidationError('Point must have 3 elements in pos')
        if kind == Kind.lineseg and pos_length != 6:
            raise ValidationError('Line segment must have 6 elements in pos')
        if kind == Kind.sphere and pos_length != 6:
            raise ValidationError('Sphere must have 6 elements in pos')
        return v

    def key(self) -> str:
        if self.kind == Kind.point:
            return f"Pt{self.pos[0]}_{self.pos[1]}_{self.pos[2]}"
        elif self.kind == Kind.lineseg:
            return f"Ln{self.pos[0]}_{self.pos[1]}_{self.pos[2]}_{self.pos[3]}_{self.pos[4]}_{self.pos[5]}"
        elif self.kind == Kind.sphere:
            return f"Sp{self.pos[0]}_{self.pos[1]}_{self.pos[2]}_{self.pos[3]}_{self.pos[4]}_{self.pos[5]}"

class AnnotationOut(BaseModel):
    user: str
    key: str
    kind: Kind
    pos: List[int]
    prop: Optional[Dict[str, Any]]
    tags: Optional[List[str]]
    title: Optional[str]
    description: Optional[str]


@router.get('/{dataset}', response_model=List[AnnotationOut])
@router.get('/{dataset}/', response_model=List[AnnotationOut], include_in_schema=False)
def get_annotations(dataset: str, groups: str = "", user: str = "", requestor: User = Depends(get_user)):
    """ Returns all annotations for the user defined by the accompanying Authorization token
        or the query string user if the requestor has admin permissions.
        Return format is JSON list with annotations and generated fields "user" and "key",
        where "key" is a user-scoped key used in DELETE requests for annotations.

        Optional query string "groups" (names separated by commas) can result in larger set 
        of annotations returned, corresponding to all annotations for the given groups.
        The groups can only be ones in which the user is a member unless user has admin permissions.
    """
    if not requestor.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read annotations on dataset {dataset}")
    if user == "":
        user = requestor.email
    elif user != requestor.email and not requestor.is_admin():
        raise HTTPException(status_code=401, detail=f"get of user {user} requires admin permissions")

    output = []
    members = set([user])
    if groups != "":
        groups_queried = set(groups.split(','))
        if len(groups_queried) > 0:
            if requestor.is_admin():
                groups_added = groups_queried
            else:
                groups_added = groups_queried.intersection(requestor.groups)
            if len(groups_added) == 0:
                raise HTTPException(status_code=400, detail=f"requestor {requestor.email} is not member of requested groups {groups_queried}")
            members.update(group_members(requestor, groups_added))
    try:
        for member in members:
            collection = firestore.get_collection([CLIO_ANNOTATIONS_V2, dataset, member])
            annotations_ref = collection.get()
            for annotation_ref in annotations_ref:
                annotation_dict = annotation_ref.to_dict()
                annotation_dict["user"] = member
                annotation_dict["key"] = annotation_ref.id
                output.append(annotation_dict)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving annotations for dataset {dataset}: {e}")
    return output

class KeyResponse(BaseModel):
    key: str

class KeyResponses(BaseModel):
    keys: List[str]

def write_annotation(dataset: str, annotation: Annotation, user: User, move_key: str = "") -> str:
    if annotation.user is None:
        annotation.user = user.email
    authorized = (annotation.user == user.email and user.can_write_own(dataset)) or \
                 (annotation.user != user.email and user.can_write_others(dataset))
    if not authorized:
        raise HTTPException(status_code=401, detail=f"no permission to add annotation for user {annotation.user} on dataset {dataset}")
    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS_V2, dataset, annotation.user])
        annotation_json = jsonable_encoder(annotation, exclude_unset=True)
        key = annotation.key()
        collection.document(key).set(annotation_json)
        if move_key != "" and move_key != key:
            collection.document(move_key).delete()
        return key
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in put annotation for dataset {dataset}")

PutResponse = Union[KeyResponse, KeyResponses]

@router.put('/{dataset}', response_model=PutResponse)
@router.post('/{dataset}', response_model=PutResponse)
@router.put('/{dataset}/', response_model=PutResponse, include_in_schema=False)
@router.post('/{dataset}/', response_model=PutResponse, include_in_schema=False)
def post_annotations(dataset: str, payload: Union[Annotation, List[Annotation]], move_key: str = "", user: User = Depends(get_user)):
    """ Allows adding annotations or moving a single annotation.  POST should be either a single
        annotation object or a list of objects.  If a single annotation, can also use 'move_key=oldkey' 
        query string to remove old annotation with key 'oldkey'.  If POSTing a list of annotations,
        the 'move_key' query string is ignored. Returns JSON with "key" or "keys" property holding
        a single key or a list of keys in the same order as the POSTed annotations.
    """
    if isinstance(payload, Annotation):
        key = write_annotation(dataset, payload, user, move_key)
        return KeyResponse(key=key)
    else:
        keys = []
        for annotation in payload:
            keys.append(write_annotation(dataset, annotation, user))
        return KeyResponses(keys=keys)

@router.delete('/{dataset}/{key}')
@router.delete('/{dataset}/{key}/', include_in_schema=False)
def delete_annotation(dataset: str, key: str, user_email: str = "", user: User = Depends(get_user)):
    """ Delete an annotation based on the key supplied in the URL. If the client has proper authorization,
        annotations for another user can be deleted by supplying the query string 'user_email=foo@bar.com'.
    """
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
