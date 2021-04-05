import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from enum import Enum
from typing import Dict, List, Any, Set, Union, Optional
from pydantic import BaseModel, ValidationError, validator

from config import *
from dependencies import get_membership, get_user, User
from stores import firestore
from google.cloud.firestore import Query

router = APIRouter()

ALLOWED_QUERY_OPS = set(['<', '<=', '==', '>', '>=', '!=', 'array-contains', 'array-contains-any', 'in', 'not-in'])

def reconcile_single_annotation(results, version, changes):
    """for given results (list of snapshsots with decreasing timestamp), return 
       best object that matches the given version
    
    Args:
        results (Query results): assumed to be ordered by descending timestamp
        version (str): version desired, assumed that later versions are 
            lexicographically larger and None or empty string return most recent
            version.
        changes (bool): if true, return all changes to the annotation.
    """
    if changes:
        output = []
        for doc in results:
            data = doc.to_dict()
            if version is None or version == "" or data["_version"] <= version:
                output.append(data)
        return output

    for doc in results:
        data = doc.to_dict()
        if version is None or version == "" or data["_version"] <= version:
            return data

    return {}

def reconcile_annotations(results, id_field: str, version, changes):
    """for given results (list of snapshsots without timestamp ordering), 
       return list of objects for given version
    
    Args:
        results (Query results): assumed to be ordered by descending timestamp
        id_field (str): field that represents id where versioning is handled across it.
        version (str): version desired, assumed that later versions are 
            lexicographically larger and None or empty string return most recent
            version.
        changes (bool): if true, return all changes to the annotation.
    """
    if changes:
        output = []
        for doc in results:
            data = doc.to_dict()
            if version is None or version == "" or data["_version"] <= version:
                output.append(data)
        return output

    best_per_id = {}
    best_timestamp_per_id = {}
    for doc in results:
        data = doc.to_dict()
        if version is None or version == "" or data["_version"] <= version:
            if id_field not in data:
                raise HTTPException(status_code=400, detail=f"id field {id_field} not present in annotation: {data}")
            id = data[id_field]
            if id not in best_per_id or data["_timestamp"] > best_timestamp_per_id[id]:
                best_per_id[id] = data
                best_timestamp_per_id[id] = data["_timestamp"]
                print(f"id {id} -> data {data}")

    return best_per_id


def write_annotation(version, collection, data, user: User):
    data["_version"] = version
    data["_timestamp"] = time.time()
    data["_user"] = user.email
    try:
        collection.add(data)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in writing annotation to version {version}: {e}\n{data}")

@router.get('/{dataset}/{annotation_type}/id-number/{id}', response_model=Union[List, dict])
@router.get('/{dataset}/{annotation_type}/id-number/{id}/', response_model=Union[List, dict], include_in_schema=False)
def get_annotations(dataset: str, annotation_type: str, id: int, version: str = "", changes: bool = False, id_field: str = "bodyid", user: User = Depends(get_user)):
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
        results = collection.where(id_field, u'==', id).order_by('_timestamp', direction=Query.DESCENDING).get()
        return reconcile_single_annotation(results, version, changes)

    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving annotations for dataset {dataset}: {e}")

class QueryRequest(BaseModel):
    fieldPath: str
    op: str
    value: Any

    @validator('op')
    def restrict_ops(cls, v):
        if v not in ALLOWED_QUERY_OPS:
            raise ValidationError("illegal query op: {v}")
        return v

@router.post('/{dataset}/{annotation_type}/query')
@router.post('/{dataset}/{annotation_type}/query/', include_in_schema=False)
def get_annotations(dataset: str, annotation_type: str, query: QueryRequest, version: str = "", changes: bool = False, id_field: str = "bodyid", user: User = Depends(get_user)):
    """ Executes a query on the annotations using supplied JSON.
        
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
        results = collection.where(query.fieldPath, query.op, query.value).get()
        output = reconcile_annotations(results, id_field, version, changes)
        return output

    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving annotations for dataset {dataset}: {e}")


@router.put('/{dataset}/{annotation_type}')
@router.post('/{dataset}/{annotation_type}')
@router.put('/{dataset}/{annotation_type}/', include_in_schema=False)
@router.post('/{dataset}/{annotation_type}/', include_in_schema=False)
def post_annotations(dataset: str, annotation_type: str, payload: Union[dict, List[dict]], version: str = "", user: User = Depends(get_user)):
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
