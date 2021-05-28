from config import *

import httpx
from fastapi import APIRouter, Depends, Response, HTTPException
from dependencies import public_dataset, get_user, User
from typing import Dict, List

from pydantic import BaseModel
from pydantic.typing import Optional
from urllib.parse import quote

from stores import firestore

router = APIRouter()

class Volume(BaseModel):
    bucket: str
    path: str
    description: Optional[str]

cache = {}  # cache of Volume data so we don't need a firestore request for every proxy
collection = firestore.get_collection(CLIO_VOLUMES)
for volume in collection.stream():
    cache[volume.id] = Volume(**volume.to_dict())

@router.post('')
@router.post('/', include_in_schema=False)
def post_volumes(volumes: Dict[str, Volume], current_user: User = Depends(get_user)):
    global cache
    if not current_user.is_admin():
        raise HTTPException(status_code=401, detail="user must be admin to set volume metadata")
    try:
        collection = firestore.get_collection(CLIO_VOLUMES)
        for volume_id, volume in volumes.items():
            data = volume.dict(exclude_unset=True)
            collection.document(volume_id).set(data)
            cache[volume_id] = Volume(**data)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail="error in POSTing volumes")

@router.delete('')
@router.delete('/', include_in_schema=False)
def delete_volumes(to_delete: List[str], current_user: User = Depends(get_user)):
    global cache
    if not current_user.is_admin():
        raise HTTPException(status_code=401, detail="user must be admin to delete volume metadata")
    try:
        collection = firestore.get_collection(CLIO_VOLUMES)
        for volume_id in to_delete:
            collection.document(volume_id).delete()
            if volume_id in cache:
                del cache[volume_id]
        # TODO -- Allow deletion of all data corresponding to this volume?
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in deleting volumes {to_delete}")

@router.get('')
@router.get('/', include_in_schema=False)
def get_volumes(current_user: User = Depends(get_user)):
    global cache
    try:
        collection = firestore.get_collection(CLIO_VOLUMES)
        volumes_out = {}
        for volume in collection.stream():
            volume_info = volume.to_dict()
            cache[volume.id] = Volume(**volume_info)
            if public_dataset(volume.id) or current_user.can_read(volume.id):
                volumes_out[volume.id] = volume_info
        return volumes_out
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail="error in retrieving volumes' metadata")

@router.get('{volume}')
@router.get('/{volume}', include_in_schema=False)
def get_volume(volume: str, current_user: User = Depends(get_user)):
    global cache
    try:
        if public_dataset(volume) or current_user.can_read(volume):
            doc_ref = firestore.get_collection(CLIO_VOLUMES).document(volume).get()
            if doc_ref.exists:
                data = doc_ref.to_dict()
                cache[volume] = Volume(**data)
                return data
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail="error in retrieving volumes' metadata")

@router.get('proxy/{volume}/{obj_path:path}')
@router.get('/proxy/{volume}/{obj_path:path}', include_in_schema=False)
async def proxy_volume(volume: str, obj_path: str) -> Response:
    # if not (public_dataset(volume) or current_user.can_read(volume)):
    #     raise HTTPException(status_code=401, detail=f"no permission to do proxy requests to dataset {volume}")
    if volume not in cache:
        raise HTTPException(status_code=400, detail=f"no volume information available for {volume}")

    # perform the proxy
    async with httpx.AsyncClient() as client:
        bucket = quote(cache[volume].bucket, safe='')
        obj = quote(cache[volume].path+'/'+obj_path, safe='')
        proxy_url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{obj}"
        proxy = await client.get(proxy_url)
    response = Response()
    response.body = proxy.content
    response.status_code = proxy.status_code
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'POST, GET, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
    return response

