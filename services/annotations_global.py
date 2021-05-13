import time

from fastapi import status, APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from enum import Enum
from typing import Dict, List, Any, Set, Union, Optional
from pydantic import BaseModel, ValidationError, validator

from config import *
from dependencies import get_user, User, version_str_to_int
from stores import firestore
from google.cloud import firestore as google_firestore

router = APIRouter()

ALLOWED_QUERY_OPS = set(['<', '<=', '==', '>', '>=', '!=', 'array_contains', 'array_contains_any', 'in', 'not_in'])

def remove_reserved_fields(data: dict):
    """Returns copy of the dict with any reserved fields removed"""
    del_list = []
    for field in data:
        if field.startswith("_"):
            del_list.append(field)
    if len(del_list) != 0:
        output = data.copy()
        for field in del_list:
            del output[field]
        return output
    return data

def check_reserved_fields(data: List[dict]):
    """Check for reserved fields and raise HTTPException if present"""
    for obj in data:
        for field in obj:
            if field.startswith("_"):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'cannot have fields starting with underscore since those names are reserved')

def get_changes(collection, head_doc, from_key: str = ""):
    """Returns a list of data corresponding to all changes (with restricted fields removed) starting at from_key"""
    start_pos = None
    output = []
    head_data = head_doc.to_dict()
    if from_key == "" or from_key == head_doc.id:
        start_pos = 0
        output = [remove_reserved_fields(head_data)]  
    else:
        for i, key in enumerate(head_data["_archived_keys"]):
            if key == from_key:
                start_pos = i
                break
    if start_pos is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f'non-existant key given "{from_key}" not in document {head_doc.id} chain')
    for key in head_data["_archived_keys"][start_pos:]:
        doc = collection.document(key).get()
        data = remove_reserved_fields(doc.to_dict())
        output.append(data)
    return output

def get_best_version(collection, doc, id_field: str, version: str):
    """Returns the best record that fulfills the given version of the doc.

    First we get the HEAD data if the current doc is not the HEAD already.
    Since the HEAD data has a list of all the versions of the children in 
    timestamp order, we can return the best version in at most an additional
    GET and at best no additional GETs because the HEAD data fulfills
    the version request (e.g., if version is empty so HEAD is requested).
    Note that there may be multiple data documents for a given version, and
    this function returns the most recent data for the given version.

    If no version is found (e.g., a version requested precedes any data),
    then a None key is returned with an empty dict.
    
    Args:
        doc: firestore Document reference.
        version (str): version desired, assumed that later versions are 
            lexicographically larger and None or empty string return most recent
            version.

    Returns: (best_key, best_data, head_doc)
        best_key (str): the key of the document that best matches version
        best_data (dict): the dict of the above key with reserved fields removed
        head_doc (firestore Document ref): the HEAD document reference
    """
    doc_data = doc.to_dict()
    if doc_data['_head']:
        head_key = doc.id
        head_doc = doc
        head_data = doc_data
    else:
        head_key = f'id{doc_data[id_field]}'
        head_doc = collection.document(head_key).get()
        head_data = head_doc.to_dict()

    if version == "":
        return (head_key, remove_reserved_fields(head_data), head_doc)

    version_int = version_str_to_int(version)
    if version_int >= head_data['_version']:
        return (head_key, remove_reserved_fields(head_data), head_doc)

    child_key = None
    for i, child_version in enumerate(head_data['_archived_versions']):
        if version_int >= child_version:
            child_key = head_data['_archived_keys']
            break

    if child_key is None:
        return (child_key, {}, head_doc)
    
    child_doc = collection.document(child_key).get()
    if child_doc is None:
        return (child_key, {}, head_doc)
    return (child_key, remove_reserved_fields(child_doc.to_dict()), head_doc)


def run_query(collection, query, id_field: str, version: str, changes: bool):
    """ Run query and get best hits for given version """
    t0 = time.perf_counter()
    output = []
    if version == "":
        head_results = query.where('_head', '==', True).stream()  # this guarantees we only get 1 hit per id
        for head_doc in head_results:
            if changes:
                output.extend(get_changes(collection, head_doc))
            else:
                head_data = remove_reserved_fields(head_doc.to_dict())
                output.append(head_data)
    else:
        query_results = query.stream()

        # filter by id because we may get multiple hits per id, so we want the hit closest to our version request.
        doc_per_key = {}
        data_per_key = {}
        version_int = version_str_to_int(version)
        for doc in query_results:
            doc_data = doc.to_dict()
            if doc_data['_version'] > version_int:
                continue
            id = doc_data[id_field]
            if id in data_per_key and \
               (doc_data['_version'] < data_per_key[id]['_version'] or \
                (doc_data['_version'] == data_per_key[id]['_version'] and doc_data['_timestamp'] < data_per_key[id]['_timestamp'])):
                continue
            data_per_key[id] = doc_data
            doc_per_key[id] = doc

        # now for best annotation per id, see if it is indeed the last for the given version
        for doc in doc_per_key.values():
            best_key, best_data, head_doc = get_best_version(collection, doc, id_field, version)
            if best_key is None or best_key != doc.id: # No record satisfies the version
                continue
            elif changes:
                output.extend(get_changes(collection, head_doc, best_key))
            else:
                output.append(best_data)

    elapsed = time.perf_counter() - t0
    print(f"Query matched {len(output)} annotations: {elapsed:0.4f} sec")
    return output


def run_query_on_ids(collection, query, ids: List[int], id_field: str, version: str, changes: bool):
    """ Run query across an arbitrary number of ids. """
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
        partial_query = query.where(id_field, op, value)
        datalist = run_query(collection, partial_query, id_field, version, changes)
        if len(datalist) != 0:
            output.extend(datalist)

    elapsed = time.perf_counter() - t0
    print(f"Ran query on {len(ids)} ids and found {len(output)} annotations that matched: {elapsed:0.4f} sec")
    return output

@google_firestore.transactional
def update_in_transaction(transaction, head_ref, archived_ref, data: dict, version: str):
    snapshot = head_ref.get(transaction=transaction)
    orig_data = snapshot.to_dict()
    if version == "":
        version_int = orig_data['_version']
    else:
        version_int = version_str_to_int(version)
    data["_version"] = version_int

    if orig_data['_version'] <= version_int:
        # new data should be HEAD so archive current snapshot
        updated = orig_data.copy()
        updated.update(data)
        orig_data['_head'] = False
        del orig_data['_archived_versions']
        del orig_data['_archived_keys']
        transaction.set(archived_ref, orig_data)
        updated['_archived_versions'].insert(0, orig_data['_version'])
        updated['_archived_keys'].insert(0, archived_ref.id)
        transaction.set(head_ref, updated)
    else:
        # new data is old so it is archived and insert into appropriate position in HEAD tracker
        data['_head'] = False
        transaction.set(archived_ref, data)
        inserted = False
        for i, v in enumerate(orig_data['_archived_versions']):
            if v <= version_int:
                orig_data['_archived_versions'].insert(i, version_int)
                orig_data['_archived_keys'].insert(i, archived_ref.id)
                inserted = True
                break
        if not inserted:
            orig_data['_archived_versions'].append(version_int)
            orig_data['_archived_keys'].append(archived_ref.id)
        transaction.set(head_ref, orig_data)


def write_annotation(collection, data: dict, id_field: str, version: str, user: User):
    """ Write annotation transactionally, modifying HEAD and archiving old annotation """
    if not id_field in data:
        raise HTTPException(status_code=400, detail=f'the id field "{id_field}" must be included in every annotation: {data}')
    id = data[id_field]
    head_key = f'id{id}'
    data["_timestamp"] = time.time()
    data["_user"] = user.email

    try:
        transaction = firestore.db.transaction()
        head_ref = collection.document(head_key)
        archived_ref = collection.document()
        update_in_transaction(transaction, head_ref, archived_ref, data, version)
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
        return run_query_on_ids(collection, collection, ids, id_field, version, changes)

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
            return run_query(collection, nonid_query, id_field, version, changes)
        
        return run_query_on_ids(collection, nonid_query, ids, id_field, version, changes)


    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving annotations for dataset {dataset}: {e}")


@router.put('/{dataset}/{annotation_type}')
@router.post('/{dataset}/{annotation_type}')
@router.put('/{dataset}/{annotation_type}/', include_in_schema=False)
@router.post('/{dataset}/{annotation_type}/', include_in_schema=False)
def post_annotations(dataset: str, annotation_type: str, payload: Union[List[Dict], Dict], id_field: str = "bodyid", version: str = "", user: User = Depends(get_user)):
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
        write_annotation(collection, annotation, id_field, version, user)
        num += 1
        if num % 100 == 0:
            print(f"Wrote {num} {annotation_type} annotations to dataset {dataset}...")
