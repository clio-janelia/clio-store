import time

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from google.auth.transport import requests
from google.oauth2 import id_token
from google.auth import exceptions
from google.cloud import firestore

from pydantic import BaseModel
from pydantic.typing import List, Set, Dict, Optional

from config import *

__USER_CACHE__ = {}
__DATASET_CACHE__ = {}

# reloads User and Dataset info from DB after this many seconds
USER_REFRESH_SECS = 600.0
DATASET_REFRESH_SECS = 600.0

class Layer(BaseModel):
    name: str      # Example: "segmentation-v1.2"
    location: str  # Example: "gs://neuroglancer-janelia-flyem-hemibrain/v1.2/segmentation"

class Dataset(BaseModel):
    description: str
    location: str
    public: Optional[bool] = False
    layers: Optional[List[Layer]] = [] 

class User(BaseModel):
    email: str
    email_verified: bool
    disabled: Optional[bool] = False
    global_roles: Optional[Set[str]] = set()
    datasets: Optional[Dict[str, Set[str]]] = {}

    def has_role(self, role, dataset: str) -> bool:
        if role in self.global_roles:
            return True
        if dataset == "":
            return False
        if role in self.datasets[dataset]:
            return True
        return False

    def can_read(self, dataset: str) -> bool:
        if "admin" in self.global_roles or "clio_general" in self.global_roles:
            return True
        if dataset in self.datasets:
            if "clio_read" in self.datasets[dataset]:
                return True
        return False
    
    def can_write(self, dataset: str) -> bool:
        if "admin" in self.global_roles or "clio_general" in self.global_roles:
            return True
        if dataset in self.datasets:
            if "clio_write" in self.datasets[dataset]:
                return True
        return False
    
    def is_admin(self) -> bool:
        return "admin" in self.global_roles

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

async def get_user_from_token(token: str = Depends(oauth2_scheme)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        idinfo = id_token.verify_oauth2_token(token, requests.Request())
        email = idinfo["email"].lower()
        email_verified = idinfo.get("email_verified", False)
    except exceptions.GoogleAuthError:
        raise credentials_exception
    except:
        print(f"no user token so using TEST_USER {TEST_USER}")
        if TEST_USER is not None:
            email = TEST_USER
            email_verified = True
        else:
            raise credentials_exception

    user = get_user_from_store(email, email_verified)
    if user is None:
        raise credentials_exception
    return user

async def get_user(current_user: User = Depends(get_user_from_token)):
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

def get_user_from_store(email: str, email_verified: bool=False, dataset: str="") -> User:
    """Check google token and return user roles.
    """
    global __USER_CACHE__

    cached_user = __USER_CACHE__.get(email, None)
    user = None
    if cached_user is not None:
        age = time.time() - cached_user.get("last_update", 0)
        if age > USER_REFRESH_SECS:
            print(f"user {email} last checked {age} secs ago... refreshing")
        else:
            print(f"user {email} has valid cache entry: {cached_user}")
            user = cached_user.get("user", None)
    
    if user is None:
        db = firestore.Client()
        data = db.collection(CLIO_USERS).document(email).get()
        datadict = data.to_dict()
        if datadict is None:
            datadict = {}
        datadict["email"] = email
        datadict["email_verified"] = email_verified
        if "clio_global" in datadict:
            datadict["global_roles"] = datadict["clio_global"]
        user = User(**datadict)
        if email == OWNER:
            user.global_roles.add("admin")
        print(f"user: {user}")
        __USER_CACHE__[email] = {
            "user": user,
            "last_update": time.time()
        }

    return user

def get_dataset(user: User, dataset_id: str) -> Dataset:
    """Returns dataset information if user has permission to read it.
    """
    global __DATASET_CACHE__

    cached_dataset = __DATASET_CACHE__.get(dataset_id, None)
    dataset = None
    if cached_dataset is not None:
        age = time.time() - cached_dataset.get("last_update", 0)
        if age > DATASET_REFRESH_SECS:
            print(f"dataset {dataset_id} last checked {age} secs ago... refreshing")
        else:
            print(f"dataset {dataset_id} has valid cache entry: {cached_dataset}")
            dataset = cached_dataset.get("dataset", None)
    
    if dataset is None:
        db = firestore.Client()
        data = db.collection(CLIO_DATASETS).document(dataset_id).get()
        data = data.to_dict()
        try:
            dataset = Dataset(**data)
        except Exception as e:
            print(e)
            raise HTTPException(status_code=400, detail=f"dataset {dataset_id} has bad metadata entry")
        print(f"dataset: {dataset}")
        __DATASET_CACHE__[dataset_id] = {
            "dataset": dataset,
            "last_update": time.time()
        }

    if dataset.public or user.can_read(dataset_id):
        return dataset
        
    raise HTTPException(status_code=401, detail=f"user does not have permission to read dataset {dataset_id}")
