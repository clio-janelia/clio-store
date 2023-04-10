from config import *
from http import HTTPStatus
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List, Union

from dependencies import get_user, User
from stores import firestore

router = APIRouter()

@router.post('')
@router.post('/', include_in_schema=False)
def site_reports(report: Union[List[Dict], Dict], user: User = Depends(get_user)):
    """Posts site reports in JSON format to storage."""
        
    key = user.email + "-" + datetime.now().strftime("%Y-%m-%d")
    try:
        collection = firestore.get_collection([CLIO_SITE_REPORTS])
        collection.document(key).set(report)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=f"error during posting of site report for {user.email}: {e}")
