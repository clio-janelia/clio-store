import time

from fastapi import status, APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from enum import Enum
from typing import Dict, List, Any, Set, Union, Optional
from pydantic import BaseModel, ValidationError, validator

from config import *
from dependencies import get_user, User
from stores import firestore
from google.cloud.firestore import Query

router = APIRouter()

ALLOWED_QUERY_OPS = set(['<', '<=', '==', '>', '>=', '!=', 'array_contains', 'array_contains_any', 'in', 'not_in'])

def remove_reserved_fields(data: dict):
    """Remove any reserved fields"""
    del_list = []
    for field in data:
        if field.startswith("_"):
            del_list.append(field)
    for field in del_list:
        del data[field]

def check_reserved_fields(data: List[dict]):
    """Check for reserved fields and raise HTTPException if present"""
    for obj in data:
        for field in obj:
            if field.startswith("_"):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"cannot have fields starting with underscore since those names are reserved")

def get_changes(results, version: str):
    output = []
    for doc in results:
        data = doc.to_dict()
        if version is None or version == "" or data["_version"] <= version:
            remove_reserved_fields(data)
            output.append(data)
    return output

def reconcile_query(results, nonid_query: dict, id_field: str, version: str):
    """Returns list of annotations for given version where annotations for each id
       have been coalesced per id.
    
    Args:
        results (Query results): list of JSON objects that meet query.
        nonid_query (dict): field constraints to be checked
        id_field (str): field that represents id where versioning is handled across it.
        version (str): version desired, assumed that later versions are 
            lexicographically larger and None or empty string return most recent
            version.
        changes (bool): if true, return all changes to the annotations.
    """
    # prune by version
    pruned_results = []
    for doc in results:
        data = doc.to_dict()
        if version is None or version == "" or data["_version"] <= version:
            if id_field not in data:
                raise HTTPException(status_code=400, detail=f"id field {id_field} not present in annotation: {data}")
            if "_timestamp" not in data:
                raise HTTPException(status_code=400, detail=f"internal timestamp not present in annotation: {data}")
            pruned_results.append(data)

    if len(pruned_results) == 0:
        return []
    elif len(pruned_results) == 1:
        remove_reserved_fields(pruned_results[0])
        return pruned_results
    
    # sort by timestamp
    pruned_results.sort(key=lambda x: x["_timestamp"])

    # determine best annotations per id field
    best = {}
    for data in pruned_results:
        id = data[id_field]
        if id not in best:
            best[id] = data
        elif data["_version"] > best[id]["_version"] or (data["_version"] == best[id]["_version"] and data["_timestamp"] > best[id]["_timestamp"]):
            best[id].update(data)
    unmatched_ids = set()
    for data in best.values():
        unmatched = False
        for field in nonid_query:
            if field in data and data[field] != nonid_query[field]:
                unmatched = True
                break
        if unmatched:
            unmatched_ids.add(data[id_field])
        else:
            remove_reserved_fields(data)
    for id in unmatched_ids:
        del best[id]
    return list(best.values())

def run_query_on_ids(collection, nonid_query: dict, ids: List[int], id_field: str, version: str, changes: bool):
    """ Run query (without id_field selector) across an arbitrary number of ids. """
    t0 = time.perf_counter()
    output = []
    for start in range(0, len(ids), 10):
        remain = min(len(ids) - start, 10)
        if remain == 1:
            value = ids[start]
            op = '=='
        else:
            value = ids[start:start+remain]
            op = 'in'
        results = collection.where(id_field, op, value).get()
        if changes:
            data = get_changes(results, version)
        else:
            data = reconcile_query(results, nonid_query, id_field, version)
        if len(data) != 0:
            output.extend(data)

    elapsed = time.perf_counter() - t0
    print(f"Ran query on {len(ids)} ids and found {len(output)} annotations that matched: {elapsed:0.4f} sec")
    return output

def get_ids_fulfilling_query(nonid_query, id_field: str, version: str):
    """ Run query (without id_field selector) and get all ids that meet criteria. """
    t0 = time.perf_counter()
    results = nonid_query.get()
    ids = set()
    for doc in results:
        data = doc.to_dict()
        if version is None or version == "" or data["_version"] <= version:
            if id_field not in data:
                raise HTTPException(status_code=400, detail=f"id field {id_field} not present in annotation: {data}")
            if "_timestamp" not in data:
                raise HTTPException(status_code=400, detail=f"internal timestamp not present in annotation: {data}")
            ids.add(data[id_field])
    elapsed = time.perf_counter() - t0
    print(f"Got {len(ids)} ids that fulfill non-id query: {elapsed:0.4f} sec")
    return list(ids)

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
def get_annotations(dataset: str, annotation_type: str, id: str, version: str = "", changes: bool = False, id_field: str = "bodyid", user: User = Depends(get_user)):
    """ Returns the neuron annotation associated with the given id.
        
    Query strings:

        version (str): If supplied, annotations are for the given dataset version.

        changes (bool): If True, returns list of changes to this annotation across all versions.

        id_field (str): The id field name (default: "bodyid")

    Returns:

        A JSON list (if changes requested or multiple ids given) or JSON object if not.
    """
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read annotations on dataset {dataset}")

    if "," in id:
        id_strs = id.split(",")
        ids = [int(id_str) for id_str in id_strs]
    else:
        ids = [int(id)]
    
    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS_GLOBAL, annotation_type, dataset])
        return run_query_on_ids(collection, {}, ids, id_field, version, changes)

    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving annotations for dataset {dataset}: {e}")

@router.post('/{dataset}/{annotation_type}/query', response_model=List[dict])
@router.post('/{dataset}/{annotation_type}/query/', response_model=List[dict], include_in_schema=False)
def get_annotations(dataset: str, annotation_type: str, query: dict, version: str = "", changes: bool = False, id_field: str = "bodyid", user: User = Depends(get_user)):
    """ Executes a query on the annotations using supplied JSON.

    The JSON query format uses field names as the keys, and desired values.
    Example:
    { "bodyid": 23, "hemilineage": "0B", ... }
    Each field value must be true, i.e., the conditions or ANDed together.
        
    Query strings:

        version (str): If supplied, annotations are for the given dataset version.

        changes (bool): If True, returns list of changes to this annotation across all versions.

        id_field (str): The id field name (default: "bodyid") that should be integers.

    Returns:

        A JSON list of objects.
    """
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read annotations on dataset {dataset}")

    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS_GLOBAL, annotation_type, dataset])
        nonid_query = collection
        ids = []
        for key in query:
            if key == id_field:
                if isinstance(query[key], int):
                    ids = [query[key]]
                elif isinstance(query[key], list):
                    ids = query[key]
                else:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"id field must be int or list of ints, got: {query[key]}")
                continue
            else:
                if isinstance(query[key], list):
                    if len(query[key]) > 10:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"currently no more than 10 values can be queried at a time")
                    if len(query[key]) == 1:  # counters apparent issue with using 'in'. TODO: determine underlying issue.
                        op = "=="
                        query[key] = query[key][0]
                    else:
                        op = "in"
                else:
                    op = "=="
                nonid_query = nonid_query.where(key, op, query[key])

        if len(ids) == 0:
            # Run query to get the ids that meet the query
            ids = get_ids_fulfilling_query(nonid_query, id_field, version)
        
        # Get all of each body id's annotations and manually test against query
        return run_query_on_ids(collection, query, ids, id_field, version, changes)


    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving annotations for dataset {dataset}: {e}")


@router.put('/{dataset}/{annotation_type}')
@router.post('/{dataset}/{annotation_type}')
@router.put('/{dataset}/{annotation_type}/', include_in_schema=False)
@router.post('/{dataset}/{annotation_type}/', include_in_schema=False)
def post_annotations(dataset: str, annotation_type: str, payload: Union[List[Dict], Dict], version: str = "v0.0", user: User = Depends(get_user)):
    """ Add either a single annotation object or a list of objects. All must be all in the 
        same dataset version.
    """
    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS_GLOBAL, annotation_type, dataset])
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in getting annotations collection for dataset {dataset}: {e}")

    if isinstance(payload, dict):
        payload = [payload]
    check_reserved_fields(payload)
    num = 0
    for annotation in payload:
        write_annotation(version, collection, annotation, user)
        num += 1
        if num % 100 == 0:
            print(f"Wrote {num} {annotation_type} annotations to dataset {dataset}...")
