import time

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from google.auth.transport import requests
from google.oauth2 import id_token
from google.auth import exceptions

from pydantic import BaseModel
from pydantic.typing import List, Set, Dict, Any, Optional

from stores import firestore
from config import *

__DATASET_CACHE__ = None
__USER_CACHE__ = None

# reloads User and Dataset info from DB after this many seconds
USER_REFRESH_SECS = 600.0
DATASET_REFRESH_SECS = 600.0

class Layer(BaseModel):
    name: str      # Example: "segmentation-v1.2"
    location: str  # Example: "gs://neuroglancer-janelia-flyem-hemibrain/v1.2/segmentation"


class User(BaseModel):
    email: str
    email_verified: bool = False
    disabled: Optional[bool] = False
    global_roles: Optional[Set[str]] = set()
    datasets: Optional[Dict[str, Set[str]]] = {}
    updated: float = time.time()

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
        dataset_roles = self.datasets(dataset, set())
        read_roles = set("clio_read", "clio_general", "clio_write")
        return read_roles & dataset_roles
    
    def can_write(self, dataset: str) -> bool:
        if "admin" in self.global_roles or "clio_general" in self.global_roles:
            return True
        return "clio_write" in self.datasets(dataset, set())
    
    def is_admin(self) -> bool:
        return "admin" in self.global_roles

class UserCache(BaseModel):
    collection: Any
    cache: Dict[str, User] = {}

    def refresh_user(self, user_ref) -> User:
        user_dict = user_ref.to_dict()
        user_dict["email"] = user_ref.id
        if "clio_global" in user_dict:
            user_dict["global_roles"] = user_dict["clio_global"]
        user_obj = User(**user_dict)
        user_obj.updated = time.time()
        if user_obj.email == OWNER:
            user_obj.global_roles.add("admin")
        self.cache[user_obj.email] = user_obj
        return user_obj

    def refresh_cache(self):
        for user_ref in self.collection.get():
            self.refresh_user(user_ref)
        print(f"Cached {len(self.cache)} user metadata.")

    def get_user(self, email: str) -> User:
        user = self.cache.get(email)
        if user is not None:
            age = time.time() - user.updated
            if age > USER_REFRESH_SECS:
                user = None
        if user is None:
            user_ref = self.collection.document(email).get()
            user = self.refresh_user(user_ref)
        return user

class Dataset(BaseModel):
    description: str
    location: str
    public: Optional[bool] = False
    layers: Optional[List[Layer]] = [] 

class DatasetCache(BaseModel):
    updated: float = time.time()
    collection: Any
    cache: Dict[str, Dataset] = {}

    def refresh_cache(self):
        datasets = self.collection.get()
        for dataset_ref in datasets:
            dataset_dict = dataset_ref.to_dict()
            dataset_obj = Dataset(**dataset_dict)
            self.cache[dataset_ref.id] = dataset_obj
        self.updated = time.time()
        print(f"Cached {len(self.cache)} dataset metadata.")

    def get_dataset(self, user: User, dataset_id: str) -> Dataset:
        """Returns dataset information if user has permission to read it."""

        age = time.time() - self.updated
        if age > DATASET_REFRESH_SECS:
            print(f"dataset cache last checked {age} secs ago... refreshing")
            self.refresh_cache()

        if dataset_id not in self.cache:
            raise HTTPException(status_code=404, detail=f"dataset {dataset_id} not found")    

        dataset =  self.cache[dataset_id]    
        if dataset.public or user.can_read(dataset_id):
            return dataset
            
        raise HTTPException(status_code=401, detail=f"user does not have permission to read dataset {dataset_id}")

def public_dataset(user: User, dataset_id: str) -> bool:
    """Returns True if the given dataset is public"""
    dataset = __DATASET_CACHE__.get_dataset(user, dataset_id)
    if dataset.public is None:
        return False
    return dataset.public

# cache everything initially on startup of service
__DATASET_CACHE__ = DatasetCache(collection = firestore.get_collection([CLIO_DATASETS]))
__DATASET_CACHE__.refresh_cache()

__USER_CACHE__ = UserCache(collection = firestore.get_collection([CLIO_USERS]))
__USER_CACHE__.refresh_cache()


# handle OAuth2

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
    except exceptions.GoogleAuthError:
        raise credentials_exception
    except:
        print(f"no user token so using TEST_USER {TEST_USER}")
        if TEST_USER is not None:
            email = TEST_USER
        else:
            raise credentials_exception

    user = get_user_from_store(email)
    if user is None:
        raise credentials_exception
    return user

async def get_user(current_user: User = Depends(get_user_from_token)):
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

def get_user_from_store(email: str) -> User:
    """Check google token and return user roles and data."""
    return __USER_CACHE__.get_user(email)

