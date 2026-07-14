# JSON Annotation support is now proxied to DVID instead of handled by 
# Firestore. Because these endpoints are specific to neurons, we 
# hardwire annotation_type to "neurons" and map to DVID keyvalue or 
# neuronjson instance "segmentation_annotations".

from dataclasses import dataclass
import json
import re
import requests

from fastapi import status, APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from typing import Dict, List, Union

from config import *
from dependencies import get_dataset, get_user, User
from stores import cache

router = APIRouter()

ALLOWED_QUERY_OPS = set(['<', '<=', '==', '>', '>=', '!=', 'array_contains', 'array_contains_any', 'in', 'not_in'])
MAX_ANNOTATIONS_RETURNED = 1000000

set_fields = set(['tags'])


@dataclass(frozen=True)
class DVIDTarget:
    server: str
    uuid: str

    @property
    def base_url(self) -> str:
        return f"{self.server}/api/node/{self.uuid}"

    @property
    def broker_url(self) -> str:
        return f"{self.server}/api/auth/clio/{self.uuid}"


def resolve_dvid_target(dataset: str, version: str = "") -> DVIDTarget:
    """Resolve and validate the one DVID node used by broker and data calls."""

    if not isinstance(version, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bad DVID UUID {version!r} used for dataset {dataset}",
        )

    # Convert a Clio version tag to its DVID UUID. Bare values are DVID UUIDs.
    if version.startswith('v'):
        tag_to_uuid = cache.get_value(
            collection_path=[CLIO_ANNOTATIONS_GLOBAL], 
            document='metadata', 
            path=['neurons', dataset, 'tag_to_uuid']
        )
        if tag_to_uuid:
            if version not in tag_to_uuid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Bad version {version} used for dataset {dataset}"
                )
            version = tag_to_uuid[version]

    dataset_cache = get_dataset(dataset)
    if len(version) == 0:
        version = dataset_cache.uuid

    if dataset_cache.dvid is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"DVID server for dataset {dataset} not found"
        )
    if not isinstance(version, str) or re.fullmatch(r"[0-9a-fA-F]+", version) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bad DVID UUID {version!r} used for dataset {dataset}",
        )
    return DVIDTarget(server=dataset_cache.dvid.rstrip('/'), uuid=version)


def _allowed_broker_return_url(request: Request):
    origin = request.headers.get("origin")
    if not origin or ALLOWED_ORIGINS == "*":
        return None
    allowed = {value.strip() for value in ALLOWED_ORIGINS.split(',') if value.strip()}
    return origin if origin in allowed else None


def _broker_error(detail: str = "DVID authorization broker unavailable"):
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)


def dvid_capability(
    target: DVIDTarget, user: User, permission: str, request: Request,
) -> str:
    """Request one node- and permission-bound capability from DVID."""
    if permission not in ("view", "edit"):
        raise ValueError(f"unsupported DVID permission {permission!r}")
    if not user.token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = {"permission": permission}
    return_url = _allowed_broker_return_url(request)
    if return_url is not None:
        payload["return_url"] = return_url
    try:
        response = requests.post(
            target.broker_url,
            json=payload,
            headers={"Authorization": f"Bearer {user.token}"},
            timeout=10,
        )
    except requests.RequestException as error:
        print(f"DVID authorization broker request failed: {error}")
        raise _broker_error()

    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if response.status_code == status.HTTP_400_BAD_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="DVID target could not be uniquely resolved",
        )
    if response.status_code != status.HTTP_200_OK:
        raise _broker_error()

    try:
        body = response.json()
    except (ValueError, requests.RequestException) as error:
        print(f"Invalid DVID authorization broker response: {error}")
        raise _broker_error()
    if not isinstance(body, dict):
        raise _broker_error()

    decision = body.get("decision")
    if decision == "allow":
        capability = body.get("capability")
        if not isinstance(capability, str) or not capability:
            raise _broker_error()
        return capability
    if decision == "tos_required":
        tos_url = body.get("tos_url")
        if not isinstance(tos_url, str) or not tos_url:
            raise _broker_error()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"decision": "tos_required", "tos_url": tos_url},
        )
    if decision == "deny":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No permission to access annotations on the resolved DVID node",
        )
    raise _broker_error()


def _capability_headers(capability: str):
    return {"Authorization": f"DVID-Capability {capability}"}


def dvid_request(url: str, capability: str, payload=None):
    print(f"Performing dvid GET {url} with payload of {len(payload) if payload else 'no'} bytes")
    if payload:
        r = requests.get(url, data=payload, headers=_capability_headers(capability))
    else:
        r = requests.get(url, headers=_capability_headers(capability))
    if r.status_code != 200:
        raise HTTPException(
            status_code=r.status_code, 
            detail=f"Error in dvid request, status {r.status_code}, {url}: {r.content}"
        )
    return r.content

async def dvid_streaming_request(url: str, capability: str, payload=None):
    print(f"Performing dvid streaming GET {url} with payload of {len(payload) if payload else 'no'} bytes")
    if payload:
        r = requests.get(url, data=payload, headers=_capability_headers(capability))
    else:
        r = requests.get(url, headers=_capability_headers(capability))
    if r.status_code != 200:
        raise HTTPException(
            status_code=r.status_code, 
            detail=f"Error in dvid request, status {r.status_code}, {url}: {r.content}"
        )
    yield r.content


def dvid_request_json(url: str, capability: str, payload=None):
    content = dvid_request(url, capability, payload)
    annot_json_str = str(content.decode()) 
    print(f"returned JSON: {annot_json_str}")
    return json.loads(annot_json_str)

def require_dataset_read(dataset: str, user: User):
    if not user.can_read(dataset):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No permission to read annotation metadata on dataset {dataset}",
        )


@router.get('/{dataset}/neurons/fields', response_model=List)
@router.get('/{dataset}/neurons/fields/', response_model=List, include_in_schema=False)
def get_fields(dataset: str, request: Request, user: User = Depends(get_user)):
    """ Returns all fields within annotations for the given scope.
        
    Returns:

        A JSON list of the fields present in at least one annotation.
    """
    target = resolve_dvid_target(dataset)
    capability = dvid_capability(target, user, "view", request)
    url = f"{target.base_url}/segmentation_annotations/fields"

    responseBytes = dvid_request(url, capability)
    return Response(content=responseBytes, media_type="application/json")


@router.get('/{dataset}/neurons/versions', response_model=dict)
@router.get('/{dataset}/neurons/versions/', response_model=dict, include_in_schema=False)
def get_versions(dataset: str, user: User = Depends(get_user)):
    """ Returns the versions for the given scope.
        
    Returns:

        A dict with tag keys and corresponding dvid UUIDs as value.
    """
    require_dataset_read(dataset, user)
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


@router.get('/{dataset}/neurons/head_tag', response_model=str)
@router.get('/{dataset}/neurons/head_tag/', response_model=str, include_in_schema=False)
def get_head_tag(dataset: str, user: User = Depends(get_user)):
    """ Returns the head version tag for the given scope.
        
    Returns:

        A string of the HEAD version tag, e.g., "v0.3.33"
    """
    require_dataset_read(dataset, user)
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


@router.get('/{dataset}/neurons/head_uuid', response_model=str)
@router.get('/{dataset}/neurons/head_uuid/', response_model=str, include_in_schema=False)
def get_head_uuid(dataset: str, user: User = Depends(get_user)):
    """ Returns the head version uuid for the given scope.
        
    Returns:

        A string of the HEAD version uuid, e.g., "74ea83"
    """
    require_dataset_read(dataset, user)
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


@router.get('/{dataset}/neurons/tag_to_uuid/{tag}', response_model=str)
@router.get('/{dataset}/neurons/tag_to_uuid/{tag}/', response_model=str, include_in_schema=False)
def get_tag_to_uuid(dataset: str, tag: str, user: User = Depends(get_user)):
    """ Returns the corresponding dvid UUID of the given tag for the given scope.
        
    Returns:

        A string of the uuid corresponding to the tag, e.g., "74ea83"
    """
    require_dataset_read(dataset, user)
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


@router.get('/{dataset}/neurons/uuid_to_tag/{uuid}', response_model=str)
@router.get('/{dataset}/neurons/uuid_to_tag/{uuid}/', response_model=str, include_in_schema=False)
def get_uuid_to_tag(dataset: str, uuid: str, user: User = Depends(get_user)):
    """ Returns the corresponding string tag for the given dvid UUID for the given scope.
        
    Returns:

        A string of the tag corresponding to the uuid, e.g., "v0.3.32"
    """
    require_dataset_read(dataset, user)
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

@router.get('/{dataset}/neurons/all')
@router.get('/{dataset}/neurons/all/', include_in_schema=False)
def get_all_annotations(dataset: str, request: Request, cursor: str = None,
                        size: int = MAX_ANNOTATIONS_RETURNED, show: str = None,
                        user: User = Depends(get_user)):
    """ Returns all current neuron annotations for the given dataset and annotation type.

    Query strings:

	    show (str):	If "user", shows *_user fields.
				    If "time", shows *_time fields.
				    If "all", shows both *_user and *_time fields.
				    If unset (default), shows neither *_user or *_time fields.

        Temporarily unavailable parameters:
            cursor (str): If supplied, annotations after the given id are sent.
            size (int): If supplied, at most this many annotations are returned.
        
    Returns:

        A JSON list of the annotations.

    """
    target = resolve_dvid_target(dataset)
    capability = dvid_capability(target, user, "view", request)
    url = f"{target.base_url}/segmentation_annotations/all"
    query_strings = []
    if show:
        query_strings.append(f"show={show}")
    if cursor:
        query_strings.append(f"cursor={cursor}&size={size}")
    if len(query_strings) > 0:
        url = url + "?" + "&".join(query_strings)

    return StreamingResponse(
        dvid_streaming_request(url, capability), media_type="application/json",
    )


@router.get('/{dataset}/neurons/id-number/{id}', response_model=List)
@router.get('/{dataset}/neurons/id-number/{id}/', response_model=List, include_in_schema=False)
def get_annotations(dataset: str, id: str, request: Request, version: str = "",
                    show: str = None, user: User = Depends(get_user)):
    """ Returns the neuron annotations associated with the given id list separated by commas.
        
    Query strings:

        version (str): If supplied, annotations are for the given dataset version (in clio format)

	    show (str):	If "user", shows *_user fields.
				    If "time", shows *_time fields.
				    If "all", shows both *_user and *_time fields.
				    If unset (default), shows neither *_user or *_time fields.

    Returns:

        A JSON list of annotations.
    """
    if "," in id:
        id_strs = id.split(",")
        ids = [int(id_str) for id_str in id_strs]
    else:
        ids = [int(id)]
    print(ids)

    target = resolve_dvid_target(dataset, version)
    capability = dvid_capability(target, user, "view", request)
    url = f"{target.base_url}/segmentation_annotations/keyvalues?json=true"
    if show:
        url += f"&show={show}"

    jsonList = json.dumps(ids)
    annotationDict = dvid_request_json(url, capability, jsonList)

    return list(annotationDict.values())

@router.delete('/{dataset}/neurons/id-number/{id}')
@router.delete('/{dataset}/neurons/id-number/{id}/', include_in_schema=False)
def delete_annotations(dataset: str, id: str, request: Request,
                       user: User = Depends(get_user)):
    """ Deletes the neuron annotation associated with the given id (requires permission).
        
    """
    target = resolve_dvid_target(dataset)
    capability = dvid_capability(target, user, "edit", request)
    url = f"{target.base_url}/segmentation_annotations/key/{id}"

    r = requests.delete(url, headers=_capability_headers(capability))
    if r.status_code != 200:
        raise HTTPException(
            status_code=r.status_code, 
            detail=f"Error in delete bodyid {id}, status {r.status_code}, {url}: {r.content}"
        )

@router.post('/{dataset}/neurons/query', response_model=List)
@router.post('/{dataset}/neurons/query/', response_model=List, include_in_schema=False)
def query_annotations(dataset: str, query: Union[List[Dict], Dict], request: Request,
                      version: str = "", show: str = "", onlyid: bool = False,
                      user: User = Depends(get_user)):
    """ Executes a query on the annotations using supplied JSON.

    The JSON query format uses field names as the keys, and desired values.
    Example:
    { "bodyid": [23, 101], "hemilineage": "0B", ... }
    Each field value must be true, i.e., the conditions or ANDed together.
    If the field value is a list, selected annotations must be a value in the list.
    For example, annotations with "bodyid" of 23 or 101 are selected in example above.

    If a list of queries (JSON object per query) is POSTed, the results for each query are ORed
    together with duplicate annotations removed.

    Query strings:

        version (str): If supplied, annotations are for the given dataset version.

	    show (str):	If "user", shows *_user fields.
				    If "time", shows *_time fields.
				    If "all", shows both *_user and *_time fields.
				    If unset (default), shows neither *_user or *_time fields.

        onlyid (bool): If true (false by default), will only return a list of id field values that match.

    Returns:

        A JSON list of objects.
    """
    target = resolve_dvid_target(dataset, version)
    capability = dvid_capability(target, user, "view", request)
    url = f"{target.base_url}/segmentation_annotations/query"

    querystr = []
    if show != "":
        querystr.append('show=' + show)
    if onlyid:
        querystr.append('onlyid=true')
    if len(querystr) > 0:
        url += '?' + '&'.join(querystr)

    r = requests.post(url, json=query, headers=_capability_headers(capability))
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.content) # make more robust depending on return
        
    return Response(content=r.content, media_type="application/json")


def write_annotation(base_url, payload, user, designated_user, conditional,
                     replace, capability):
    if "bodyid" not in payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=f"Cannot POST annotation when no bodyid field exists in JSON"
        )
    print(f'User in annotation write: {user.email}')
    url = f'{base_url}/segmentation_annotations/key/{payload["bodyid"]}'
    querystr = []
    if conditional != "" or replace:
        if conditional != "":
            querystr.append('conditional=' + conditional)
        if replace:
            querystr.append('replace=true')
    if designated_user is not None and designated_user != "":
        querystr.append(f'u={designated_user}')
    elif user.email is not None and user.email != "":
        querystr.append(f'u={user.email}')
    url += '?' + '&'.join(querystr)
        
    r = requests.post(url, json=payload, headers=_capability_headers(capability))
    if r.status_code != 200:
        raise HTTPException(
            status_code=r.status_code, 
            detail=f"Error in writing bodyid {payload['bodyid']}, status {r.status_code}, {url}: {r.content}"
        )

@router.put('/{dataset}/neurons')
@router.post('/{dataset}/neurons')
@router.put('/{dataset}/neurons/', include_in_schema=False)
@router.post('/{dataset}/neurons/', include_in_schema=False)
def post_annotations(dataset: str, payload: Union[List[Dict], Dict], request: Request,
                     replace: bool = False, conditional: str = "", version: str = "",
                     designated_user: str = "", user: User = Depends(get_user)):
    """ Add either a single annotation object or a list of objects. All must be all in the 
        same dataset version.

        Query strings:

        replace (bool): If True (default False), posted values replace existing ones, so any non-existing
            fields are removed.

        conditional (str): A field name or list of names separated by commas that should only be written
            if the field is currently non-existant or empty.

        version (str): The clio tag string corresponding to a version, e.g., "v0.3.1"

        designated_user (str): If supplied, the user field is set to this value instead of the 
            authenticated user.
    """
    target = resolve_dvid_target(dataset, version)
    capability = dvid_capability(target, user, "edit", request)
    print(f"base_url: {target.base_url}")

    if isinstance(payload, dict):
        write_annotation(
            target.base_url, payload, user, designated_user, conditional,
            replace, capability,
        )
    else: # must be list
        for annotation in payload:
            write_annotation(
                target.base_url, annotation, user, designated_user, conditional,
                replace, capability,
            )
