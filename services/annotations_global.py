import time
import json

from fastapi import status, APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from enum import Enum
from typing import Dict, List, Any, Set, Union, Optional
from pydantic import BaseModel, ValidationError

from config import *
from dependencies import get_dataset, get_user, User, version_str_to_int
from stores import firestore, cache
from google.cloud import firestore as google_firestore

router = APIRouter()

ALLOWED_QUERY_OPS = set(['<', '<=', '==', '>', '>=', '!=', 'array_contains', 'array_contains_any', 'in', 'not_in'])

set_fields = set(['tags'])

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


def run_query(collection, query, id_field: str, version: str, changes: bool, onlyid: bool = False):
    """ Run query and get best hits for given version """
    t0 = time.perf_counter()
    output = []
    if version == "":
        head_results = query.where('_head', '==', True).stream()  # this guarantees we only get 1 hit per id
        for head_doc in head_results:
            if onlyid:
                doc_dict = head_doc.to_dict()
                if id_field in doc_dict:
                    output.append(doc_dict[id_field])
            elif changes:
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
            elif onlyid:
                if id_field in best_data:
                    output.append(best_data[id_field])
            elif changes:
                output.extend(get_changes(collection, head_doc, best_key))
            else:
                output.append(best_data)

    elapsed = time.perf_counter() - t0
    print(f"Query matched {len(output)} annotations: {elapsed:0.4f} sec")
    return output


def run_query_on_ids(collection, query, ids: List[int], id_field: str, version: str, changes: bool, onlyid: bool = False):
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
        datalist = run_query(collection, partial_query, id_field, version, changes, onlyid)
        if len(datalist) != 0:
            output.extend(datalist)

    elapsed = time.perf_counter() - t0
    print(f"Ran query on {len(ids)} ids and found {len(output)} annotations that matched: {elapsed:0.4f} sec")
    return output

@google_firestore.transactional
def update_in_transaction(transaction, head_ref, archived_ref, data: dict, conditional_fields: List[str], version: str):
    snapshot = head_ref.get(transaction=transaction)
    if not snapshot.exists:
        # first record for this body so create new HEAD
        data['_head'] = True
        data['_archived_versions'] = []
        data['_archived_keys'] = []
        if version == "":
            data['_version'] = 0
        else:
            data['_version'] = version_str_to_int(version)
        transaction.set(head_ref, data)
        transaction.delete(archived_ref)  # don't need it
        return

    orig_data = snapshot.to_dict()
    if version == "":
        version_int = orig_data['_version']
    else:
        version_int = version_str_to_int(version)
    data["_version"] = version_int

    # if there are conditional fields that already exist, delete them.
    for field in conditional_fields:
        if field in data and field in orig_data and bool(orig_data[field]):
            del data[field]

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


def write_annotation(collection, data: dict, id_field: str, conditional: List[str], version: str, user: User):
    """ Write annotation transactionally, modifying HEAD and archiving old annotation """
    if not id_field in data:
        raise HTTPException(status_code=400, detail=f'the id field "{id_field}" must be included in every annotation: {data}')
    id = data[id_field]
    head_key = f'id{id}'
    data["_timestamp"] = time.time()
    data["_user"] = user.email

    new_fields = False
    fields = cache.get_value(collection_path=[CLIO_ANNOTATIONS_GLOBAL], document='metadata', path=['neurons', 'VNC', 'fields'])
    for cur_field in data:
        if not cur_field.startswith('_'):
            if cur_field not in fields:
                fields.append(cur_field)
                new_fields = True
    if new_fields:
        cache.set_value(collection_path=[CLIO_ANNOTATIONS_GLOBAL], document='metadata', value=fields, path=['neurons', 'VNC', 'fields'])

    try:
        transaction = firestore.db.transaction()
        head_ref = collection.document(head_key)
        archived_ref = collection.document()
        update_in_transaction(transaction, head_ref, archived_ref, data, conditional, version)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in writing annotation to version {version}: {e}\n{data}")

@router.get('/{dataset}/{annotation_type}/fields', response_model=List)
@router.get('/{dataset}/{annotation_type}/fields/', response_model=List, include_in_schema=False)
def get_fields(dataset: str, annotation_type: str, user: User = Depends(get_user)):
    """ Returns all fields within annotations for the given scope.
        
    Returns:

        A JSON list of the fields present in at least one annotation.
    """
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read annotations on dataset {dataset}")

    fields = cache.get_value(collection_path=[CLIO_ANNOTATIONS_GLOBAL], document='metadata', path=['neurons', 'VNC', 'fields'])
    if not fields:
        raise HTTPException(status_code=404, detail=f"Could not find any fields for annotation type {annotation_type} in dataset {dataset}")
    return fields

@router.get('/{dataset}/{annotation_type}/versions', response_model=dict)
@router.get('/{dataset}/{annotation_type}/versions/', response_model=dict, include_in_schema=False)
def get_versions(dataset: str, annotation_type: str, user: User = Depends(get_user)):
    """ Returns the versions for the given scope.
        
    Returns:

        A dict with tag keys and corresponding dvid UUIDs as value.
    """
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read annotations on dataset {dataset}")

    tag_to_uuid = cache.get_value(collection_path=[CLIO_ANNOTATIONS_GLOBAL], document='metadata', path=['neurons', 'VNC', 'tag_to_uuid'])
    if not tag_to_uuid:
        raise HTTPException(status_code=404, detail=f"Could not find any tag_to_uuid for annotation type {annotation_type} in dataset {dataset}")
    return tag_to_uuid

@router.get('/{dataset}/{annotation_type}/head_tag', response_model=str)
@router.get('/{dataset}/{annotation_type}/head_tag/', response_model=str, include_in_schema=False)
def get_head_tag(dataset: str, annotation_type: str, user: User = Depends(get_user)):
    """ Returns the head version tag for the given scope.
        
    Returns:

        A string of the HEAD version tag, e.g., "v0.3.33"
    """
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read annotations on dataset {dataset}")

    head_tag = cache.get_value(collection_path=[CLIO_ANNOTATIONS_GLOBAL], document='metadata', path=['neurons', 'VNC', 'head_tag'])
    if not head_tag:
        raise HTTPException(status_code=404, detail=f"Could not find any head_tag for annotation type {annotation_type} in dataset {dataset}")
    return head_tag

@router.get('/{dataset}/{annotation_type}/head_uuid', response_model=str)
@router.get('/{dataset}/{annotation_type}/head_uuid/', response_model=str, include_in_schema=False)
def get_head_uuid(dataset: str, annotation_type: str, user: User = Depends(get_user)):
    """ Returns the head version uuid for the given scope.
        
    Returns:

        A string of the HEAD version uuid, e.g., "74ea83"
    """
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read annotations on dataset {dataset}")

    head_uuid = cache.get_value(collection_path=[CLIO_ANNOTATIONS_GLOBAL], document='metadata', path=['neurons', 'VNC', 'head_uuid'])
    if not head_uuid:
        raise HTTPException(status_code=404, detail=f"Could not find any head_uuid for annotation type {annotation_type} in dataset {dataset}")
    return head_uuid

@router.get('/{dataset}/{annotation_type}/tag_to_uuid/{tag}', response_model=str)
@router.get('/{dataset}/{annotation_type}/tag_to_uuid/{tag}/', response_model=str, include_in_schema=False)
def get_tag_to_uuid(dataset: str, annotation_type: str, tag: str, user: User = Depends(get_user)):
    """ Returns the corresponding dvid UUID of the given tag for the given scope.
        
    Returns:

        A string of the uuid corresponding to the tag, e.g., "74ea83"
    """
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read annotations on dataset {dataset}")

    tag_to_uuid = cache.get_value(collection_path=[CLIO_ANNOTATIONS_GLOBAL], document='metadata', path=['neurons', 'VNC', 'tag_to_uuid'])
    if not tag_to_uuid:
        raise HTTPException(status_code=404, detail=f"Could not find any tag_to_uuid for annotation type {annotation_type} in dataset {dataset}")
    if tag not in tag_to_uuid:
        raise HTTPException(status_code=404, detail=f"Could not find tag {tag} for annotation type {annotation_type} in dataset {dataset}")
    return tag_to_uuid[tag]

@router.get('/{dataset}/{annotation_type}/uuid_to_tag/{uuid}', response_model=str)
@router.get('/{dataset}/{annotation_type}/uuid_to_tag/{uuid}/', response_model=str, include_in_schema=False)
def get_uuid_to_tag(dataset: str, annotation_type: str, uuid: str, user: User = Depends(get_user)):
    """ Returns the corresponding string tag for the given dvid UUID for the given scope.
        
    Returns:

        A string of the tag corresponding to the uuid, e.g., "v0.3.32"
    """
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read annotations on dataset {dataset}")

    uuid_to_tag = cache.get_value(collection_path=[CLIO_ANNOTATIONS_GLOBAL], document='metadata', path=['neurons', 'VNC', 'uuid_to_tag'])
    if not uuid_to_tag:
        raise HTTPException(status_code=404, detail=f"Could not find any uuid_to_tag for annotation type {annotation_type} in dataset {dataset}")
    found_tag = None
    num_found = 0
    for stored_uuid in uuid_to_tag:
        if len(stored_uuid) < len(uuid) and uuid.startswith(stored_uuid):
            num_found += 1
            found_tag = uuid_to_tag[stored_uuid]
        if len(stored_uuid) >= len(uuid) and stored_uuid.startswith(uuid):
            num_found += 1
            found_tag = uuid_to_tag[stored_uuid]
    if num_found > 1:
        raise HTTPException(status_code=400, detail=f"uuid {uuid} is ambiguous because more than one hit for annotation type {annotation_type} in dataset {dataset}")
    if not found_tag:
        raise HTTPException(status_code=404, detail=f"Could not find uuid {uuid} for annotation type {annotation_type} in dataset {dataset}")
    return found_tag

async def annotation_streamer(collection):
    # t0 = time.time()
    pagesize = 1000
    total = 0
    prepend = '['
    cursor = None
    while True:
        query = collection.limit(pagesize).order_by('__name__')
        if cursor:
            query = query.start_after({"__name__": cursor})
        retrieved = 0
        for snapshot in query.stream():
            retrieved += 1
            total += 1
            annotation = remove_reserved_fields(snapshot.to_dict())
            yield prepend + json.dumps(annotation)
            cursor = snapshot.id
            prepend = ','
        # print(f'{retrieved} retrieved, {total} total processed in {time.time() - t0} secs')
        if retrieved < pagesize:
            break
    yield ']'

@router.get('/{dataset}/{annotation_type}/all')
@router.get('/{dataset}/{annotation_type}/all/', include_in_schema=False)
def get_all_annotations(dataset: str, annotation_type: str, user: User = Depends(get_user)):
    """ Returns all current neuron annotations for the given dataset and annotation type.
        
    Returns:

        A JSON list of the annotations.

    """
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read annotations on dataset {dataset}")

    try:
        collection = firestore.get_collection([CLIO_ANNOTATIONS_GLOBAL, annotation_type, dataset]).where('_head', '==', True)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving annotations for dataset {dataset}: {e}")
    
    return StreamingResponse(annotation_streamer(collection), media_type='application/json')

    
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
    if version != "":
        cur_dataset = get_dataset(dataset)
        if cur_dataset.tag == version:
            version = ""

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

@router.post('/{dataset}/{annotation_type}/query', response_model=List)
@router.post('/{dataset}/{annotation_type}/query/', response_model=List, include_in_schema=False)
def get_annotations(dataset: str, annotation_type: str, query: dict, version: str = "", changes: bool = False, \
                    id_field: str = "bodyid", onlyid: bool = False, user: User = Depends(get_user)):
    """ Executes a query on the annotations using supplied JSON.

    The JSON query format uses field names as the keys, and desired values.
    Example:
    { "bodyid": 23, "hemilineage": "0B", ... }
    Each field value must be true, i.e., the conditions or ANDed together.
        
    Query strings:

        version (str): If supplied, annotations are for the given dataset version.

        changes (bool): If True, returns list of changes to this annotation across all versions.

        id_field (str): The id field name (default: "bodyid") that should be integers.

        onlyid (bool): If true (false by default), will only return a list of id field values that match.

    Returns:

        A JSON list of objects.
    """
    if not user.can_read(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to read annotations on dataset {dataset}")
    if version != "":
        cur_dataset = get_dataset(dataset)
        if cur_dataset.tag == version:
            version = ""

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
                if key in set_fields:
                    op = "array_contains"
                elif isinstance(query[key], list):
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
            return run_query(collection, nonid_query, id_field, version, changes, onlyid)
        
        return run_query_on_ids(collection, nonid_query, ids, id_field, version, changes, onlyid)


    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in retrieving annotations for dataset {dataset}: {e}")

#conditional: Union[List[str], str] = []
@router.put('/{dataset}/{annotation_type}')
@router.post('/{dataset}/{annotation_type}')
@router.put('/{dataset}/{annotation_type}/', include_in_schema=False)
@router.post('/{dataset}/{annotation_type}/', include_in_schema=False)
def post_annotations(dataset: str, annotation_type: str, payload: Union[List[Dict], Dict], id_field: str = "bodyid", \
                     conditional: str = "", version: str = "", user: User = Depends(get_user)):
    """ Add either a single annotation object or a list of objects. All must be all in the 
        same dataset version.

        Query strings:

        id_field (str): The field name that corresponds to the id, e.g., "bodyid"

        conditional (str): A field name or list of names separated by commas that should only be written
            if the field is currently non-existant or empty.

        version (str): The clio tag string corresponding to a version, e.g., "v0.3.1"
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
    conditional_fields = []
    if bool(conditional):
        conditional_fields = conditional.split(',')
    for annotation in payload:
        write_annotation(collection, annotation, id_field, conditional_fields, version, user)
        num += 1
        if num % 100 == 0:
            print(f"Wrote {num} {annotation_type} annotations to dataset {dataset}...")
