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

@router.get('')
@router.get('/', include_in_schema=False)
def get_datasets(current_user: User = Depends(get_user)):
    try:
        collection = firestore.get_collection(CLIO_DATASETS)
        datasets_out = {}
        for dataset in collection.stream():
            dataset_info = dataset.to_dict()
            if public_dataset(dataset.id) or current_user.can_read(dataset.id):
                datasets_out[dataset.id] = dataset_info
        return datasets_out
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail="error in retrieving datasets' metadata")

@router.get('{dataset}')
@router.get('/{dataset}', include_in_schema=False)
def get_dataset(dataset: str, current_user: User = Depends(get_user)):
    try:
        if public_dataset(dataset) or current_user.can_read(dataset):
            doc_ref = firestore.get_collection(CLIO_DATASETS).document(dataset).get()
            if doc_ref.exists:
                return doc_ref.to_dict()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail="error in retrieving datasets' metadata")

