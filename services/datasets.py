from config import *

from fastapi import APIRouter, Depends, HTTPException
from dependencies import public_dataset, get_user, User
from pydantic.typing import List

from google.cloud import firestore
from google.cloud import storage

router = APIRouter()

@router.post('')
@router.post('/', include_in_schema=False)
def post_datasets(datasets: dict, current_user: User = Depends(get_user)):
    if not current_user.is_admin():
        raise HTTPException(status_code=401, detail="user must be admin to set dataset metadata")
    try:
        db = firestore.Client()
        for dataset_id, dataset in datasets.items():
            db.collection(CLIO_DATASETS).document(dataset_id).set(dataset)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail="error in POSTing datasets")

@router.delete('')
@router.delete('/', include_in_schema=False)
def delete_datasets(to_delete: List[str], current_user: User = Depends(get_user)):
    if not current_user.is_admin():
        raise HTTPException(status_code=401, detail="user must be admin to delete dataset metadata")
    try:
        db = firestore.Client()
        for dataset_id in to_delete:
            db.collection(CLIO_DATASETS).document(dataset_id).delete()
        # TODO -- Allow deletion of all data corresponding to this dataset?
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error in deleting datasets {to_delete}")

@router.get('')
@router.get('/', include_in_schema=False)
def get_datasets(current_user: User = Depends(get_user)):
    try:
        db = firestore.Client()
        datasets = db.collection(CLIO_DATASETS).get()
        datasets_out = {}
        for dataset in datasets:
            dataset_info = dataset.to_dict()
            if public_dataset(dataset.id) or current_user.can_read(dataset.id):
                datasets_out[dataset.id] = dataset_info
        return datasets_out
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail="error in retrieving datasets' metadata")

