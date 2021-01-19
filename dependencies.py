import time

from fastapi import FastAPI, Depends, HTTPException, status, Request, Response
from fastapi.security import OAuth2PasswordBearer
from fastapi.routing import APIRoute

from typing import Callable

from google.auth.transport import requests
from google.oauth2 import id_token
from google.auth import exceptions

from pydantic import BaseModel
from pydantic.typing import List, Set, Dict, Any, Optional

from config import *
from stores import firestore

__DATASET_CACHE__ = None

# stores reference to global APP
app = FastAPI()

# handle CORS
@app.options('/{rest_of_path:path}')
async def preflight_handler(request: Request, rest_of_path: str) -> Response:
    response = Response()
    response.headers['Access-Control-Allow-Origin'] = ALLOWED_ORIGINS
    response.headers['Access-Control-Allow-Methods'] = 'POST, GET, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
    return response

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
    tag: Optional[str]

class DatasetCache(BaseModel):
    collection: Any
    cache: Dict[str, Dataset] = {}
    public_datasets: Set[str] = set()
    updated: float = time.time() # update time for all dataset

    def refresh_cache(self):
        datasets = self.collection.get()
        for dataset_ref in datasets:
            dataset_dict = dataset_ref.to_dict()
            dataset_obj = Dataset(**dataset_dict)
            self.cache[dataset_ref.id] = dataset_obj
            if dataset_obj.public:
                self.public_datasets.add(dataset_ref.id)
        self.updated = time.time()
        print(f"Cached {len(self.cache)} dataset metadata.")

    def get_dataset(self, dataset_id: str) -> Dataset:
        """Returns dataset information."""
        age = time.time() - self.updated
        if age > DATASET_REFRESH_SECS:
            print(f"dataset cache last checked {age} secs ago... refreshing")
            self.refresh_cache()

        if dataset_id not in self.cache:
            raise HTTPException(status_code=404, detail=f"dataset {dataset_id} not found")    

        return self.cache[dataset_id]    

    def is_public(self, dataset_id: str) -> bool:
        """Returns True if dataset is public."""
        age = time.time() - self.updated
        if age > DATASET_REFRESH_SECS:
            print(f"dataset cache last checked {age} secs ago... refreshing")
            self.refresh_cache()

        return dataset_id in self.public_datasets    

def public_dataset(dataset_id: str) -> bool:
    """Returns True if the given dataset is public"""
    return __DATASET_CACHE__.is_public(dataset_id)

# cache everything initially on startup of service
__DATASET_CACHE__ = DatasetCache(collection = firestore.get_collection([CLIO_DATASETS]))
__DATASET_CACHE__.refresh_cache()

class User(BaseModel):
    email: str
    # email_verified: bool = False
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
        if role == "clio_general" and dataset in __DATASET_CACHE__.public_datasets:
            return True
        return False

    def can_read(self, dataset: str) -> bool:
        if "clio_general" in self.global_roles:
            return True
        if dataset in __DATASET_CACHE__.public_datasets:
            return True
        dataset_roles = self.datasets(dataset, set())
        read_roles = set("clio_read", "clio_general", "clio_write")
        return read_roles & dataset_roles
    
    def can_write_own(self, dataset: str) -> bool:
        if "clio_general" in self.global_roles:
            return True
        if dataset in __DATASET_CACHE__.public_datasets:
            return True
        dataset_roles = self.datasets(dataset, set())
        write_roles = set("clio_general", "clio_write")
        return write_roles & dataset_roles
    
    def can_write_others(self, dataset: str) -> bool:
        if "clio_write" in self.global_roles:
            return True
        return "clio_write" in self.datasets.get(dataset, set())
    
    def is_admin(self) -> bool:
        return "admin" in self.global_roles

class UserCache(BaseModel):
    collection: Any
    cache: Dict[str, User] = {}
    updated: Dict[str, float] = {} # per user update time

    def cache_user(self, user: User):
        self.updated[user.email] = time.time()
        if user.email == OWNER:
            user.global_roles.add("admin")
        self.cache[user.email] = user

    def uncache_user(self, email: str):
        if email in self.cache:
            del self.cache[email]

    def refresh_user(self, user_ref) -> User:
        user_dict = user_ref.to_dict()
        user_dict["email"] = user_ref.id
        user_obj = User(**user_dict)
        self.cache_user(user_obj)
        return user_obj

    def refresh_cache(self) -> Dict[str, User]:
        users = {}
        for user_ref in self.collection.get():
            users[user_ref.id] = self.refresh_user(user_ref)
        print(f"Cached {len(self.cache)} user metadata.")
        return users

    def get_user(self, email: str) -> User:
        user = self.cache.get(email)
        if user is not None:
            age = time.time() - self.updated.get(email, 0)
            if age > USER_REFRESH_SECS:
                user = None
        if user is None:
            user_ref = self.collection.document(email).get()
            user = self.refresh_user(user_ref)
        return user


users = UserCache(collection = firestore.get_collection([CLIO_USERS]))
users.refresh_cache()

# handle OAuth2

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

async def get_user_from_token(token: str = Depends(oauth2_scheme)) -> User:
    """Check google token and return user roles and data."""
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

    user = users.get_user(email)
    if user is None:
        raise credentials_exception
    return user

async def get_user(current_user: User = Depends(get_user_from_token)):
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

