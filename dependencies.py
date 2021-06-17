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

import jwt

# stores reference to global APP
app = FastAPI()

# handle CORS preflight requests
@app.options('/{rest_of_path:path}', include_in_schema=False)
async def preflight_handler(request: Request, rest_of_path: str) -> Response:
    response = Response()
    response.headers['Access-Control-Allow-Origin'] = ALLOWED_ORIGINS
    response.headers['Access-Control-Allow-Methods'] = 'POST, GET, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type, Range'
    return response

# set CORS headers
@app.middleware("http")
async def add_CORS_header(request: Request, call_next):
    response = await call_next(request)
    response.headers['Access-Control-Allow-Origin'] = ALLOWED_ORIGINS
    response.headers['Access-Control-Allow-Methods'] = 'POST, GET, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type, Range'
    return response
   
def version_str_to_int(version_str: str) -> int:
    """Returns a version integer given a semantic versioning string."""
    parts = version_str.split('.')
    if len(parts) > 3:
        raise HTTPException(status_code=400, detail=f'version tag "{version_str}" should only have 3 parts (major, minor, patch numbers)')
    elif len(parts) == 0:
        raise HTTPException(status_code=400, detail=f'version tag "{version_str}" should have at least major number')
    major = 0
    minor = 0
    patch = 0
    try:
        if parts[0][0] == 'v':
            major = int(parts[0][1:])
        else:
            major = int(parts[0])
        if len(parts) > 1:
            minor = int(parts[1])
        if len(parts) > 2:
            patch = int(parts[2])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'unable to parse version tag "{version_str}": {e}')
    
    return major * 1000 * 1000 + minor * 1000 + patch


# reloads User and Dataset info from DB after this many seconds
USER_REFRESH_SECS = 600.0
MEMBERSHIPS_REFRESH_SECS = 600.0
DATASET_REFRESH_SECS = 600.0

class Dataset(BaseModel):
    title: Optional[str]
    description: str

    tag: Optional[str]
    uuid: Optional[str]

    mainLayer: Optional[str]
    neuroglancer: Optional[dict]
    versions: Optional[list]

    bodyAnnotationSchema: Optional[dict]
    orderedLayers: Optional[list]

    # legacy -- will be removed after UI accomodates new schema
    public: Optional[bool] = False
    layers: Optional[List[dict]] = []  # segmentation refs
    dimensions: Optional[dict]
    position: Optional[List[float]]
    crossSectionScale: Optional[float]
    projectionScale: Optional[float]
    location: Optional[str] # legacy grayscale image ref that will be moved to layers with type=image.

class DatasetCache(BaseModel):
    collection: Any
    cache: Dict[str, Dataset] = {}
    public: Set[str] = set()
    updated: float = time.time() # update time for all dataset

    def refresh_cache(self):
        datasets = self.collection.get()
        for dataset_ref in datasets:
            dataset_dict = dataset_ref.to_dict()
            dataset_obj = Dataset(**dataset_dict)
            self.cache[dataset_ref.id] = dataset_obj
            if dataset_obj.public:
                self.public.add(dataset_ref.id)
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

        return dataset_id in self.public    

def public_dataset(dataset_id: str) -> bool:
    """Returns True if the given dataset is public"""
    return datasets.is_public(dataset_id)

def get_dataset(dataset_id: str) -> Dataset:
    """Returns dataset given the dataset id"""
    return datasets.get_dataset(dataset_id)

# cache everything initially on startup of service
datasets = DatasetCache(collection = firestore.get_collection([CLIO_DATASETS]))
datasets.refresh_cache()

def get_dataset(dataset_id: str) -> Dataset:
    return datasets.get_dataset(dataset_id)

class User(BaseModel):
    email: str
    # email_verified: bool = False
    name: Optional[str]  # full name
    org: Optional[str]   # affiliated organization
    disabled: Optional[bool] = False
    global_roles: Optional[Set[str]] = set()
    datasets: Optional[Dict[str, Set[str]]] = {}
    groups: Optional[Set[str]] = set()

    def has_role(self, role: str, dataset: str = "") -> bool:
        if role in self.global_roles:
            return True
        if dataset == "":
            return False
        if dataset in self.datasets and role in self.datasets[dataset]:
            return True
        if role == "clio_general" and dataset in datasets.public:
            return True
        return False

    def can_read(self, dataset: str = "") -> bool:
        if "clio_general" in self.global_roles:
            return True
        if dataset in datasets.public:
            return True
        dataset_roles = self.datasets.get(dataset, set())
        read_roles = set(["clio_read", "clio_general", "clio_write"])
        return read_roles & dataset_roles
    
    def can_write_own(self, dataset: str = "") -> bool:
        if "clio_general" in self.global_roles:
            return True
        if dataset in datasets.public:
            return True
        dataset_roles = self.datasets.get(dataset, set())
        write_roles = set(["clio_general", "clio_write"])
        return write_roles & dataset_roles
    
    def can_write_others(self, dataset: str = "") -> bool:
        if "clio_write" in self.global_roles:
            return True
        return "clio_write" in self.datasets.get(dataset, set())
    
    def is_admin(self) -> bool:
        return "admin" in self.global_roles

class UserCache(BaseModel):
    collection: Any # users collection
    cache: Dict[str, User] = {}
    user_updated: Dict[str, float] = {}   # update time per user
    memberships: Dict[str, Set[str]] = {} # set of user emails per group names
    memberships_updated: float = 0.0      # last full update of memberships

    def cache_user(self, user: User):
        self.user_updated[user.email] = time.time()
        for group in user.groups:
            if group in self.memberships:
                self.memberships[group].add(user.email)
            else:
                self.memberships[group] = set([user.email])
        if user.email == OWNER:
            user.global_roles.add("admin")
        self.cache[user.email] = user

    def uncache_user(self, email: str):
        if email in self.cache:
            user = self.cache[email]
            for group in user.groups:
                if group in self.memberships:
                    self.memberships[group].discard(email)
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
        self.memberships_updated == time.time()
        print(f"Cached {len(self.cache)} user metadata and {len(self.memberships)} groups")
        return users

    def get_user(self, email: str) -> User:
        user = self.cache.get(email)
        if user is not None:
            age = time.time() - self.user_updated.get(email, 0)
            if age > USER_REFRESH_SECS:
                user = None
        if user is None:
            user_ref = self.collection.document(email).get()
            if user_ref.exists:
                user = self.refresh_user(user_ref)
            else:
                user = User(email=email)
        return user

    def group_members(self, user: User, groups: Set[str]) -> Set[str]:
        """Returns set of emails for groups given user belongs unless user is admin"""
        if not user.is_admin():
            groups.intersection_update(user.groups)
        if len(groups) == 0:
            return set()
        age = time.time() - self.memberships_updated
        if age > MEMBERSHIPS_REFRESH_SECS:
            self.refresh_cache()
        members = set()
        for group in groups:
            if group in self.memberships:
                members.update(self.memberships[group])
        return members

users = UserCache(collection = firestore.get_collection([CLIO_USERS]))
users.refresh_cache()

def group_members(user: User, groups: Set[str]) -> Set[str]:
    """
    Return set of email addresses of members who are within the given groups
    of the given user.  Only groups to which the user belongs are added to
    the returned set.
    """
    return users.group_members(user, groups)

# handle OAuth2

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

def get_user_from_token(token: str = Depends(oauth2_scheme)) -> User:
    """Check token (either FlyEM or Google identity) and return user roles and data."""
    email = None
    if FLYEM_SECRET:
        try:
            decoded = jwt.decode(token, FLYEM_SECRET, algorithms="HS256")
            exp = decoded.get('exp', 0)
            if time.time() <= exp:
                email = decoded.get('email', None)
        except:
            pass

    if not email:
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

def get_user(current_user: User = Depends(get_user_from_token)):
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

