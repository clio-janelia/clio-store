from config import *

from fastapi import APIRouter, Depends, HTTPException
from dependencies import public_dataset, get_user, User, Dataset
from typing import Dict, List

from stores import firestore

router = APIRouter()

@router.post('')
@router.post('/', include_in_schema=False)
def post_datasets(datasets: Dict[str, Dataset], current_user: User = Depends(get_user)):
    if not current_user.is_admin():
        raise HTTPException(status_code=401, detail="user must be admin to set dataset metadata")
    try:
        collection = firestore.get_collection(CLIO_DATASETS)
        for dataset_id, dataset in datasets.items():
            collection.document(dataset_id).set(dataset.dict(exclude_unset=True))
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail="error in POSTing datasets")

@router.delete('')
@router.delete('/', include_in_schema=False)
def delete_datasets(to_delete: List[str], current_user: User = Depends(get_user)):
    if not current_user.is_admin():
        raise HTTPException(status_code=401, detail="user must be admin to delete dataset metadata")
    try:
        collection = firestore.get_collection(CLIO_DATASETS)
        for dataset_id in to_delete:
            collection.document(dataset_id).delete()
        # TODO -- Allow deletion of all data corresponding to this dataset?
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in deleting datasets {to_delete}")

def replace_element(data, tag, uuid):
    if isinstance(data, str):
        if tag and '{tag}' in data:
            data = data.replace('{tag}', tag)
        if uuid and '{uuid}' in data:
            data = data.replace('{uuid}', uuid)
        return data
    elif isinstance(data, list):
        for i, v in enumerate(data):
            data[i] = replace_element(v, tag, uuid)
    elif isinstance(data, dict):
        if tag:
            delete_keys = []
            new_kv = {}
            for k, v in data.items():
                if isinstance(k, str) and '{tag}' in k:
                    new_kv[k.replace('{tag}', tag)] = v
                    delete_keys.append(k)
                if isinstance(v, str) and '{tag}' in v:
                    data[k] = v.replace('{tag}', tag)
                elif isinstance(v, dict) or isinstance(v, List):
                    data[k] = replace_element(v, tag, uuid)
            for k in delete_keys:
                del data[k]
            data.update(new_kv)
            
        if uuid:
            delete_keys = []
            new_kv = {}
            for k, v in data.items():
                if isinstance(k, str) and '{uuid}' in k:
                    new_kv[k.replace('{uuid}', uuid)] = v
                    delete_keys.append(k)
                if isinstance(v, str) and '{uuid}' in v:
                    data[k] = v.replace('{uuid}', uuid)
                elif isinstance(v, dict) or isinstance(v, List):
                    data[k] = replace_element(v, tag, uuid)
            for k in delete_keys:
                del data[k]
            data.update(new_kv)
    return data 

def replace_templates(data):
    """Replace any instance of {tag} or {uuid} in keys or values"""
    if not isinstance(data, dict):
        return data
    tag = None
    if 'tag' in data:
        tag = data['tag']
    uuid = None
    if 'uuid' in data:
        uuid = data['uuid']
    return replace_element(data, tag, uuid)

@router.get('')
@router.get('/', include_in_schema=False)
def get_datasets(current_user: User = Depends(get_user)):
    try:
        collection = firestore.get_collection(CLIO_DATASETS)
        datasets_out = {}
        for dataset in collection.stream():
            dataset_info = dataset.to_dict()
            if public_dataset(dataset.id) or current_user.can_read(dataset.id):
                datasets_out[dataset.id] = replace_templates(dataset_info)
        return datasets_out
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail="error in retrieving datasets' metadata")

@router.get('/{dataset}')
@router.get('/{dataset}/', include_in_schema=False)
def get_dataset(dataset: str, current_user: User = Depends(get_user)):
    try:
        if public_dataset(dataset) or current_user.can_read(dataset):
            doc_ref = firestore.get_collection(CLIO_DATASETS).document(dataset).get()
            if doc_ref.exists:
                return replace_templates(doc_ref.to_dict())
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail="error in retrieving datasets' metadata")

