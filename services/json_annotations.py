# JSON Annotation support is now proxied to DVID instead of handled by 
# Firestore. Because these endpoints are specific to neurons, we 
# hardwire annotation_type to "neurons" and map to DVID keyvalue or 
# neuronjson instance "segmentation_annotations".

import time
import json
import requests

from fastapi import status, APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse

from enum import Enum
from typing import Dict, List, Any, Set, Union, Optional
from pydantic import BaseModel, ValidationError

from config import *
from dependencies import get_dataset, get_user, User, version_str_to_int
from stores import firestore, cache
from google.cloud import firestore as google_firestore

import kv_pb2

router = APIRouter()

ALLOWED_QUERY_OPS = set(['<', '<=', '==', '>', '>=', '!=', 'array_contains', 'array_contains_any', 'in', 'not_in'])
MAX_ANNOTATIONS_RETURNED = 1000000

set_fields = set(['tags'])

def dvid_base_url(dataset: str, version: str) -> str:
    """Return the DVID base URL (e.g., https://dvid.org/api/node/uuid) """

    # Convert to DVID UUID unless this doesn't have a 'v' prefix
    if version.startswith('v'):
        tag_to_uuid = cache.get_value(
            collection_path=[CLIO_ANNOTATIONS_GLOBAL], 
            document='metadata', 
            path=['neurons', dataset, 'tag_to_uuid']
        )
        if tag_to_uuid:
            version = tag_to_uuid[version]

    # Default to DVID server HEAD if no version indicated
    if len(version) == 0:
        dataset_cache = get_dataset(dataset)
        version = dataset_cache.uuid    

    # Construct the base url based on the dvid server for the dataset
    cur_dataset = get_dataset(dataset)
    if cur_dataset.dvid is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"DVID server for dataset {dataset} not found"
        )
    return f"{cur_dataset.dvid}/api/node/{version}"


def dvid_request(url: str, payload=None):
    print(f"Performing GET {url} with payload of {len(payload)} bytes")
    if payload:
        r = requests.get(url, data=payload)
    else:
        r = requests.get(url)
    if r.status_code != 200:
        raise HTTPException(
            status_code=r.status_code, 
            detail=f"Error in dvid request, status {r.status_code}, {url}: {r.content}"
        )
    return r.content


def dvid_request_json(url: str, payload=None):
    content = dvid_request(url, payload)
    annot_json_str = str(content.decode()) # + "}" # handle issue 356 before correcting it in dvid
    return json.loads(annot_json_str)


def can_read(func):
    def wrapper(self, *args, **kwargs):
        dataset = args[0]
        user = kwargs['user']
        if not user.can_read(dataset):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, 
                detail=f"no permission to read annotations on dataset {dataset}"
            )
        return func(self, *args, **kwargs)
    return wrapper


@can_read
@router.get('/{dataset}/neurons/fields', response_model=List)
@router.get('/{dataset}/neurons/fields/', response_model=List, include_in_schema=False)
def get_fields(dataset: str, annotation_type: str, user: User = Depends(get_user)):
    """ Returns all fields within annotations for the given scope.
        
    Returns:

        A JSON list of the fields present in at least one annotation.
    """
    fields = cache.get_value(
        collection_path=[CLIO_ANNOTATIONS_GLOBAL], 
        document='metadata',
        path=['neurons', dataset, 'fields']
    )
    if not fields:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"Could not find any fields for annotation type neurons in dataset {dataset}"
        )
    return fields


@can_read
@router.get('/{dataset}/neurons/versions', response_model=dict)
@router.get('/{dataset}/neurons/versions/', response_model=dict, include_in_schema=False)
def get_versions(dataset: str, annotation_type: str, user: User = Depends(get_user)):
    """ Returns the versions for the given scope.
        
    Returns:

        A dict with tag keys and corresponding dvid UUIDs as value.
    """
    tag_to_uuid = cache.get_value(
        collection_path=[CLIO_ANNOTATIONS_GLOBAL], 
        document='metadata', 
        path=['neurons', dataset, 'tag_to_uuid']
    )
    if not tag_to_uuid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"Could not find any tag_to_uuid for annotation type neurons in dataset {dataset}"
        )
    return tag_to_uuid


@can_read
@router.get('/{dataset}/neurons/head_tag', response_model=str)
@router.get('/{dataset}/neurons/head_tag/', response_model=str, include_in_schema=False)
def get_head_tag(dataset: str, annotation_type: str, user: User = Depends(get_user)):
    """ Returns the head version tag for the given scope.
        
    Returns:

        A string of the HEAD version tag, e.g., "v0.3.33"
    """
    head_tag = cache.get_value(
        collection_path=[CLIO_ANNOTATIONS_GLOBAL], 
        document='metadata', 
        path=['neurons', dataset, 'head_tag']
    )
    if not head_tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"Could not find any head_tag for annotation type neurons in dataset {dataset}"
        )
    return head_tag


@can_read
@router.get('/{dataset}/neurons/head_uuid', response_model=str)
@router.get('/{dataset}/neurons/head_uuid/', response_model=str, include_in_schema=False)
def get_head_uuid(dataset: str, annotation_type: str, user: User = Depends(get_user)):
    """ Returns the head version uuid for the given scope.
        
    Returns:

        A string of the HEAD version uuid, e.g., "74ea83"
    """
    head_uuid = cache.get_value(
        collection_path=[CLIO_ANNOTATIONS_GLOBAL], 
        document='metadata', 
        path=['neurons', dataset, 'head_uuid']
    )
    if not head_uuid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"Could not find any head_uuid for annotation type neurons in dataset {dataset}"
        )
    return head_uuid


@can_read
@router.get('/{dataset}/neurons/tag_to_uuid/{tag}', response_model=str)
@router.get('/{dataset}/neurons/tag_to_uuid/{tag}/', response_model=str, include_in_schema=False)
def get_tag_to_uuid(dataset: str, annotation_type: str, tag: str, user: User = Depends(get_user)):
    """ Returns the corresponding dvid UUID of the given tag for the given scope.
        
    Returns:

        A string of the uuid corresponding to the tag, e.g., "74ea83"
    """
    tag_to_uuid = cache.get_value(
        collection_path=[CLIO_ANNOTATIONS_GLOBAL], 
        document='metadata', 
        path=['neurons', dataset, 'tag_to_uuid']
    )
    if not tag_to_uuid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"Could not find any tag_to_uuid for annotation type neurons in dataset {dataset}"
        )
    if tag not in tag_to_uuid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"Could not find tag {tag} for annotation type neurons in dataset {dataset}"
        )
    return tag_to_uuid[tag]


@can_read
@router.get('/{dataset}/neurons/uuid_to_tag/{uuid}', response_model=str)
@router.get('/{dataset}/neurons/uuid_to_tag/{uuid}/', response_model=str, include_in_schema=False)
def get_uuid_to_tag(dataset: str, annotation_type: str, uuid: str, user: User = Depends(get_user)):
    """ Returns the corresponding string tag for the given dvid UUID for the given scope.
        
    Returns:

        A string of the tag corresponding to the uuid, e.g., "v0.3.32"
    """
    uuid_to_tag = cache.get_value(
        collection_path=[CLIO_ANNOTATIONS_GLOBAL], 
        document='metadata', 
        path=['neurons', dataset, 'uuid_to_tag']
    )
    if not uuid_to_tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"Could not find any uuid_to_tag for neurons in dataset {dataset}"
        )
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=f"uuid {uuid} is ambiguous because > 1 hit for neurons in dataset {dataset}"
        )
    if not found_tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"Could not find uuid {uuid} for neurons in dataset {dataset}"
        )
    return found_tag

@can_read
@router.get('/{dataset}/neurons/all')
@router.get('/{dataset}/neurons/all/', include_in_schema=False)
def get_all_annotations(dataset: str, annotation_type: str, cursor: str = None, 
                        size: int = MAX_ANNOTATIONS_RETURNED, user: User = Depends(get_user)):
    """ Returns all current neuron annotations for the given dataset and annotation type.

    Query strings:

        cursor (str): If supplied, annotations after the given id are sent.

        size (int): If supplied, at most this many annotations are returned.
        
    Returns:

        A JSON list of the annotations.

    """
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


def get_dvid_annotations(dataset: str, version: str, ids: List[int]):
    """ Returns all current neuron annotations for the given dataset at the Clio version
        
    Returns:

        A JSON list of the annotations.
    """
    base_url = dvid_base_url(dataset, version)
    url = f"{base_url}/segmentation_annotations/keyvalues"

    # Create payload of protobuf encoded ids
    keys = kv_pb2.Keys()
    for id in ids:
        keys.keys.append(str(id))
    content = dvid_request(url, keys.SerializeToString())

    # Decipher the returned protobuf
    keyvalues = kv_pb2.KeyValues()
    keyvalues.ParseFromString(content)

    try:
        # annotations = {}
        # for kv in keyvalues.kvs:
        #     annotations[kv.key] = json.loads(kv.value) if kv.value else None
        json_out = "["
        for kv in keyvalues.kvs:
            json_out += f'{kv.value.decode() if kv.value else "{}"},'
        json_out = json_out[:-1] + "]"
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unable to decode id {str(kv.key)} as JSON: {str(kv.value)}"
        )
    return Response(content=json_out, media_type="application/json")

@can_read
@router.get('/{dataset}/neurons/id-number/{id}', response_model=Union[List, dict])
@router.get('/{dataset}/neurons/id-number/{id}/', response_model=Union[List, dict], include_in_schema=False)
def get_annotations(dataset: str, id: str, version: str = "", user: User = Depends(get_user)):
    """ Returns the neuron annotation associated with the given id.
        
    Query strings:

        version (str): If supplied, annotations are for the given dataset version (in clio format)

    Returns:

        A JSON list (if changes requested or multiple ids given) or JSON object if not.
    """
    if "," in id:
        id_strs = id.split(",")
        ids = [int(id_str) for id_str in id_strs]
    else:
        ids = [int(id)]
    
    return get_dvid_annotations(dataset, version, ids)


@router.delete('/{dataset}/neurons/id-number/{id}')
@router.delete('/{dataset}/neurons/id-number/{id}/', include_in_schema=False)
def delete_annotations(dataset: str, id: str, user: User = Depends(get_user)):
    """ Deletes the neuron annotation associated with the given id (requires permission).
        
    """
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


def merge_annotations(merged: list, current: list, id_field: str):
    """Merge list of annotations (dict) such that in the list, the id_field field is unique."""
    if len(current) == 0:
        return
    past_ids = set()
    for annotation in merged:
        if id_field in annotation:
            past_ids.add(annotation[id_field])
    for annotation in current:
        if id_field in annotation and annotation[id_field] not in past_ids:
            merged.append(annotation)


@can_read
@router.post('/{dataset}/neurons/query', response_model=List)
@router.post('/{dataset}/neurons/query/', response_model=List, include_in_schema=False)
def get_annotations(dataset: str, annotation_type: str, query: Union[List[Dict], Dict], version: str = "",
                    onlyid: bool = False, user: User = Depends(get_user)):
    """ Executes a query on the annotations using supplied JSON.

    The JSON query format uses field names as the keys, and desired values.
    Example:
    { "bodyid": 23, "hemilineage": "0B", ... }
    Each field value must be true, i.e., the conditions or ANDed together.

    If a list of queries (JSON object per query) is POSTed, the results for each query are ORed
    together with duplicate annotations removed.
        
    Query strings:

        version (str): If supplied, annotations are for the given dataset version.

        onlyid (bool): If true (false by default), will only return a list of id field values that match.

    Returns:

        A JSON list of objects.
    """
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.put('/{dataset}/neurons')
@router.post('/{dataset}/neurons')
@router.put('/{dataset}/neurons/', include_in_schema=False)
@router.post('/{dataset}/neurons/', include_in_schema=False)
def post_annotations(dataset: str, annotation_type: str, payload: Union[List[Dict], Dict], 
                     replace: bool = False, conditional: str = "", version: str = "", user: User = Depends(get_user)):
    """ Add either a single annotation object or a list of objects. All must be all in the 
        same dataset version.

        Query strings:

        replace (bool): If True (default False), posted values replace existing ones, so any non-existing
            fields are removed.

        conditional (str): A field name or list of names separated by commas that should only be written
            if the field is currently non-existant or empty.

        version (str): The clio tag string corresponding to a version, e.g., "v0.3.1"
    """
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)
