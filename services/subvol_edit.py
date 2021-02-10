import time

from fastapi import APIRouter, Depends, HTTPException

from typing import List, Optional
from pydantic import BaseModel, validator, ValidationError

# import cloudvolume
# from cloudvolume import CloudVolume

from config import *
from dependencies import get_user, get_dataset, User
from stores import firestore

router = APIRouter()

CLIO_SUBVOL_BUCKET = os.environ.get("CLIO_SUBVOL_BUCKET", None)
CLIO_SUBVOL_WIDTH = os.environ.get("CLIO_SUBVOL_WIDTH", 256)

class SubVolume(BaseModel):
    """SubVolume centered on 3d point with segmentation in given layer id"""
    focus: List[int]
    layer: int = 0

    @validator('focus')
    def prop_is_3d(cls, v):
        if len(v) != 3:
            raise ValidationError(f"focus must be of length 3, not {len(v)}")
        return v

    def destination(self, email: str) -> str:
        if CLIO_SUBVOL_BUCKET is None:
            raise HTTPException(status_code=400, detail=f"CLIO_SUBVOL_BUCKET env var must be set for use of these endpoints")
        key = f"{self.focus[0]}_{self.focus[1]}_{self.focus[2]}_width_{CLIO_SUBVOL_WIDTH}"
        return f"{CLIO_SUBVOL_BUCKET}/{email}/{key}"

    # def bbox(self) -> cloudvolume.Bbox:
    #     pts = self.minpt.copy()
    #     pts.extend(self.maxpt)
    #     return cloudvolume.Bbox.from_list(pts)

@router.post('/{dataset}/edit')
@router.post('/{dataset}/edit/', include_in_schema=False)
def edit_subvol(dataset: str, subvol: SubVolume, user: User = Depends(get_user)):
    """
    Creates a neuroglancer precomputed subvolume cutout given bounding box.  Returns
    python script that can be downloaded and used to open and then submit change to Clio store.
    """
    if not user.can_read(dataset) or not user.can_write_own(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to edit subvolume in dataset {dataset}")
    try:
        dataset_obj = get_dataset(dataset)
        # cv = CloudVolume(dataset_obj.location)
        # vol_dest = subvol.destination(user.email)
        # vol_bounds = subvol.bbox()
        # cv.transfer_to(vol_dest, vol_bounds)

    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error editing subvolume for dataset {dataset}: {e}")

    # TODO == construct python script using info above and return it
    # print(f"src {dataset_obj.location} -> dst {vol_dest}, bounds {vol_bounds}")

@router.post('/{dataset}/submit')
@router.post('/{dataset}/submit/', include_in_schema=False)
def submit_subvol(dataset: str, payload: dict, user: User = Depends(get_user)):
    """
    Stores reference to an edited neuroglancer subvolume.
    """
    if not user.can_write_own(dataset):
        raise HTTPException(status_code=401, detail=f"no permission to submit subvolume in dataset {dataset}")
    try:        
        payload["_timestamp"] = time.time()
        collection = firestore.get_collection([CLIO_SUBVOL, user.email, "submissions"])
        collection.document().set(payload)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"error submitting subvolume for dataset {dataset}: {e}")
