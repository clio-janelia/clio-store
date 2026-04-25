"""User management has moved to DatasetGateway.

These endpoints exist only to return a clear 501 to legacy callers.
"""

from fastapi import APIRouter, HTTPException

from config import DSG_URL

router = APIRouter()


def _moved():
    raise HTTPException(
        status_code=501,
        detail=f"User management has moved to DatasetGateway ({DSG_URL})",
    )


@router.get('')
@router.get('/', include_in_schema=False)
def get_users():
    _moved()


@router.post('')
@router.post('/', include_in_schema=False)
def post_users():
    _moved()


@router.delete('')
@router.delete('/', include_in_schema=False)
def delete_users():
    _moved()
