import time

from config import *
from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies import get_user, User
from stores import firestore

router = APIRouter()

class PullRequest(BaseModel):
    pull: str

@router.post('')
@router.post('/', include_in_schema=False)
def pull_request(req: PullRequest, user: User = Depends(get_user)):
    """Copies user's pull request to protected area for later processing.

       POSTed body should be JSON with pull string equal to path to kv with scope and key:
       
       {"post": "kv/scope/key"}
    """
    elems = req.pull.split('/')
    if len(elems) != 3:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=f"badly formatted pull request '{req.pull}'")
    if elems[0] == 'kv':
        scope = elems[1]
        key = elems[2]
    else:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=f"only /kv pull requests supported at this time")

    try:
        collection = firestore.get_collection([CLIO_KEYVALUE, user.email, scope])
        value_ref = collection.document(key).get()
        if not value_ref.exists:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=f"no pull request found corresponding to {req.pull}")
        pulldata = value_ref.to_dict()

        collection = firestore.get_collection([CLIO_PULL_REQUESTS, user.email, scope])
        pulldata["_timestamp"] = time.time()
        collection.document(key).set(pulldata)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=f"error attempting pull request: {e}")

@router.get('')
@router.get('/', include_in_schema=False)
def pull_request(user_email: str, user: User = Depends(get_user)):
    """Returns user's pull requests.  If "user_email" query string is provided, that user's pull
       request is returned if the requester has sufficient permission.
       
       {"kv/scope1/key1": value1, "kv/scope2/key2": value2}
    """
    if user_email == "":
        user_email = user.email
    authorized = (user_email == user.email or user.is_admin())
    if not authorized:
        raise HTTPException(status_code=401, detail=f"no permission to access pull requests for user {user_email}")
    user_prs = {}
    try:       
        collection = firestore.get_collection([CLIO_PULL_REQUESTS])
        user_ref = collection.document(user_email).get()
        # if not user_ref.exists:
        #     raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=f"no pull requests found for {user_email}")
        pr_types = user_ref.reference.collections()
        print(f"For user {user_email}: {pr_types}")
        print(f"")
        for pr_type in pr_types:
            prs = pr_type.stream()
            cur_prs = []
            for pr in prs:
                cur_prs.append(pr.to_dict())
            user_prs[pr_type.id] = cur_prs
    except HTTPException as e:
        raise e
    except Exception as e:
        print(e)
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=f"error attempting pull request: {e}")

    return user_prs