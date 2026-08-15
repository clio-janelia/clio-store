import time

from fastapi import FastAPI, Depends, HTTPException, status, Request, Response
from fastapi.security import OAuth2PasswordBearer

from pydantic import BaseModel
from typing import List, Set, Dict, Any, Mapping, Optional

from config import *
from stores import firestore

import httpx
from starlette.concurrency import run_in_threadpool

# stores reference to global APP
app = FastAPI()

def _cors_origin(request: Request) -> str:
    """Return the origin to use in Access-Control-Allow-Origin.

    When ALLOWED_ORIGINS is '*', reflect the request's Origin header so that
    credentials: 'include' works (browsers reject wildcard with credentials).
    When ALLOWED_ORIGINS is a comma-separated list, only reflect if the
    request Origin is in the list.
    """
    origin = request.headers.get("origin", "")
    if ALLOWED_ORIGINS == "*":
        return origin or "*"
    allowed = [o.strip() for o in ALLOWED_ORIGINS.split(",")]
    if origin in allowed:
        return origin
    return allowed[0] if allowed else "*"

def _set_cors_headers(response: Response, request: Request):
    response.headers['Access-Control-Allow-Origin'] = _cors_origin(request)
    response.headers['Access-Control-Allow-Methods'] = 'POST, GET, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type, Range'
    response.headers['Access-Control-Allow-Credentials'] = 'true'

# handle CORS preflight requests
@app.options('/{rest_of_path:path}', include_in_schema=False)
async def preflight_handler(request: Request, rest_of_path: str) -> Response:
    response = Response()
    _set_cors_headers(response, request)
    return response

# set CORS headers
@app.middleware("http")
async def add_CORS_header(request: Request, call_next):
    response = await call_next(request)
    _set_cors_headers(response, request)
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

class NeuprintServer(BaseModel):
    dataset: str # What the dataset is called in the neuprint server
    server: str  # name.domain.org

class Dataset(BaseModel):
    title: Optional[str]
    description: str

    tag: Optional[str]
    uuid: Optional[str]
    dvid: Optional[str] # The base URL including http or https for dvid server.

    mainLayer: Optional[str]
    neuroglancer: Optional[dict]
    versions: Optional[list]

    typing: Optional[dict]
    neuprintHTTP: Optional[NeuprintServer]

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

class User(BaseModel):
    email: str

    name: Optional[str]
    org: Optional[str]
    picture: Optional[str] = None  # profile image URL (e.g., Google avatar)
    disabled: Optional[bool] = False
    service_account: bool = False
    global_roles: Optional[Set[str]] = set()
    datasets: Optional[Dict[str, Set[str]]] = {}
    datasets_ignore_tos: Optional[Dict[str, Set[str]]] = {}
    groups: Optional[Set[str]] = set()

    token: Optional[str] = None

    class Config:
        fields = {"token": {"exclude": True}}

    def has_role(self, role: str, dataset: str = "") -> bool:
        if self.is_admin():
            return True
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
        if self.is_admin():
            return True
        if "clio_general" in self.global_roles:
            return True
        if dataset in datasets.public:
            return True
        dataset_roles = self.datasets.get(dataset, set())
        read_roles = set(["clio_read", "clio_general", "clio_write"])
        return read_roles & dataset_roles

    def can_read_ignore_tos(self, dataset: str = "") -> bool:
        if self.is_admin():
            return True
        if self.can_read(dataset):
            return True
        dataset_roles = self.datasets_ignore_tos.get(dataset, set())
        read_roles = set(["clio_read", "clio_general", "clio_write"])
        return read_roles & dataset_roles

    def can_write_own(self, dataset: str = "") -> bool:
        if self.is_admin():
            return True
        if "clio_general" in self.global_roles:
            return True
        if dataset in datasets.public:
            return True
        dataset_roles = self.datasets.get(dataset, set())
        write_roles = set(["clio_general", "clio_write"])
        return write_roles & dataset_roles

    def can_write_others(self, dataset: str = "") -> bool:
        if self.is_admin():
            return True
        if "clio_write" in self.global_roles:
            return True
        return "clio_write" in self.datasets.get(dataset, set())

    def is_dataset_admin(self, dataset: str = "") -> bool:
        if self.is_admin():
            return True
        dataset_roles = self.datasets.get(dataset, set())
        return set(["dataset_admin"]) & dataset_roles

    def is_admin(self) -> bool:
        return "admin" in self.global_roles


print(f"DatasetGateway auth enabled: {DSG_URL}")

# token -> (timestamp, User)
_dsg_user_cache: Dict[str, Any] = {}

# group_name -> (timestamp, set of emails)
_dsg_group_members_cache: Dict[str, Any] = {}


def _resolve_token(request: Request, token: str) -> str:
    """Extract auth token from Bearer header, dsg_token cookie, or query param."""
    if token:
        return token
    cookie_token = request.cookies.get("dsg_token")
    if cookie_token:
        return cookie_token
    query_token = request.query_params.get("dsg_token")
    if query_token:
        return query_token
    return None


def _dsg_entry_for_dataset_id(dataset_id: str) -> Dict[str, str]:
    """Turn a Firestore dataset id into the native DSG decision vocabulary."""
    if ":" in dataset_id:
        name, version = dataset_id.split(":", 1)
        if name and version:
            return {"name": name, "version": version, "permission": "view"}
    return {"name": dataset_id, "permission": "view"}


def _map_dsg_roles_to_clio_roles(dsg_roles: Any) -> Set[str]:
    """Map a DSG-native decision's roles to clio's preserved User interface."""
    clio_roles = set()
    for role in dsg_roles or []:
        if role == "view":
            clio_roles.add("clio_general")
        elif role == "edit":
            clio_roles.add("clio_write")
        elif role == "admin":
            clio_roles.add("dataset_admin")
        elif role != "manage":
            clio_roles.add(role)
    return clio_roles


def _auth_service_error(error: Exception) -> HTTPException:
    print(f"DatasetGateway request failed: {error}")
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Auth service unavailable",
    )


def _credentials_error(detail: str = "Could not validate credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _fetch_dsg_identity(resolved_token: str) -> Dict[str, Any]:
    """Fetch an email-bearing principal from DSG's native identity endpoint."""
    try:
        resp = httpx.get(
            f"{DSG_URL}/api/dsg/v1/user",
            headers={"Authorization": f"Bearer {resolved_token}"},
            timeout=10,
        )
    except httpx.RequestError as error:
        raise _auth_service_error(error)

    if resp.status_code != 200:
        raise _credentials_error()

    identity = resp.json()
    if not isinstance(identity, dict):
        raise _auth_service_error(ValueError("invalid native identity response"))
    if identity.get("email") in (None, ""):
        raise _credentials_error("DatasetGateway identity is missing an email address")
    return identity


def _fetch_dsg_decisions(
    resolved_token: str, entries: List[Dict[str, str]], return_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch and correlate ordered native DSG authorization decisions."""
    payload: Dict[str, Any] = {"service": "clio", "entries": entries}
    if return_url is not None:
        payload["return_url"] = return_url
    try:
        resp = httpx.post(
            f"{DSG_URL}/api/dsg/v1/authorize",
            json=payload,
            headers={"Authorization": f"Bearer {resolved_token}"},
            timeout=10,
        )
    except httpx.RequestError as error:
        raise _auth_service_error(error)

    if resp.status_code != 200:
        raise _credentials_error()

    body = resp.json()
    decisions = body.get("entries") if isinstance(body, dict) else None
    if not isinstance(decisions, list) or len(decisions) != len(entries):
        raise _auth_service_error(ValueError("native authorization response length mismatch"))

    for entry, decision in zip(entries, decisions):
        if not isinstance(decision, dict) or (
            decision.get("name"), decision.get("version")
        ) != (entry.get("name"), entry.get("version")):
            raise _auth_service_error(ValueError("native authorization response correlation mismatch"))
    return decisions


def _build_user(
    identity: Dict[str, Any], dataset_ids: List[str], decisions: List[Dict[str, Any]],
) -> User:
    """Build clio's stable User interface from native DSG identity and decisions."""
    global_roles = {"admin"} if identity.get("admin") else set()
    email = identity["email"]
    service_account = bool(identity.get("service_account"))
    if not service_account and OWNER and email == OWNER:
        global_roles.add("admin")

    dataset_roles: Dict[str, Set[str]] = {}
    dataset_roles_ignore_tos: Dict[str, Set[str]] = {}
    for dataset_id, decision in zip(dataset_ids, decisions):
        roles = _map_dsg_roles_to_clio_roles(decision.get("roles"))
        decision_type = decision.get("decision")
        if decision_type == "allow":
            dataset_roles[dataset_id] = roles
            dataset_roles_ignore_tos[dataset_id] = roles
        elif decision_type == "tos_required":
            dataset_roles_ignore_tos[dataset_id] = roles
        elif decision_type == "service_eval":
            print(f"DSG requires service evaluation for dataset {dataset_id}")

    return User(
        email=email,
        name=identity.get("name", ""),
        picture=identity.get("picture_url"),
        service_account=service_account,
        global_roles=global_roles,
        datasets=dataset_roles,
        datasets_ignore_tos=dataset_roles_ignore_tos,
        groups=set(identity.get("groups") or []),
    )


def _fetch_dsg_user(resolved_token: str) -> User:
    """Refresh the cached clio User from DSG native identity and one batch."""
    identity = _fetch_dsg_identity(resolved_token)
    dataset_ids = list(datasets.cache)
    entries = [_dsg_entry_for_dataset_id(dataset_id) for dataset_id in dataset_ids]
    decisions = _fetch_dsg_decisions(resolved_token, entries)
    user = _build_user(identity, dataset_ids, decisions)
    user.token = resolved_token
    _dsg_user_cache[resolved_token] = (time.time(), user)
    return user


def _evict_other_user_tokens(email: str, keep: str) -> None:
    """Drop a user's *other* cached token entries.

    One user holds more than one token at a time: the ``dsg_token`` session
    cookie (used by /profile) and the stable long-lived API token (used as a
    Bearer by /v2 data calls such as /v2/neuprint). The cache is keyed by token,
    so a forced refresh on one token leaves the others stale — which is how a
    just-accepted TOS stays invisible to /v2 routes for up to USER_REFRESH_SECS,
    even though DSG reflects the acceptance immediately. After a forced /profile
    refresh, evict the user's other entries so the next request on any token
    re-reads fresh.
    """
    stale = [
        t for t, (_, u) in list(_dsg_user_cache.items())
        if t != keep and getattr(u, "email", None) == email
    ]
    for t in stale:
        _dsg_user_cache.pop(t, None)


def refresh_user(user: User) -> User:
    """Force a fresh DSG read for ``user``'s token, bypassing the cache.

    Used on an authorization denial so a TOS the user just accepted (which DSG
    reflects immediately, but this token's cached permission set may not yet
    if the /profile refresh ran against a different token) doesn't produce a
    spurious 403. Falls back to the original user if the refresh fails, so
    callers still raise their normal error.
    """
    if not user.token:
        return user
    try:
        return _fetch_dsg_user(user.token)
    except HTTPException:
        return user


def _get_user_from_dsg(request: Request, token: str) -> User:
    """Authenticate via DatasetGateway and return a clio-store User."""
    resolved_token = _resolve_token(request, token)
    if not resolved_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # /profile drives browser returns after TOS acceptance. Force a fresh DSG
    # read there so users do not loop on stale authorization state.
    force_refresh = request.url.path == "/profile"

    cached = _dsg_user_cache.get(resolved_token)
    if not force_refresh and cached and time.time() - cached[0] < USER_REFRESH_SECS:
        return cached[1]

    user = _fetch_dsg_user(resolved_token)

    # A forced refresh only updates this token's entry, but the user's *other*
    # tokens (e.g. the long-lived Bearer used by /v2/neuprint) would stay stale
    # and keep denying access on a freshly accepted TOS. Evict them so the next
    # call on any of the user's tokens re-reads.
    if force_refresh:
        _evict_other_user_tokens(user.email, keep=resolved_token)

    return user


def _dsg_group_members(user: User, groups: Set[str]) -> Set[str]:
    """Fetch group members from DatasetGateway."""
    if not user.is_admin():
        groups = groups & user.groups
    if not groups:
        return set()

    members = set()
    for group_name in groups:
        cached = _dsg_group_members_cache.get(group_name)
        if cached and time.time() - cached[0] < MEMBERSHIPS_REFRESH_SECS:
            members.update(cached[1])
            continue
        try:
            resp = httpx.get(
                f"{DSG_URL}/api/dsg/v1/groups/{group_name}/members",
                headers={"Authorization": f"Bearer {user.token}"},
                timeout=10,
            )
        except httpx.RequestError:
            continue
        if resp.status_code == 200:
            group_emails = set(resp.json())
            _dsg_group_members_cache[group_name] = (time.time(), group_emails)
            members.update(group_emails)
    return members


def group_members(user: User, groups: Set[str]) -> Set[str]:
    """Return set of email addresses of members within the given groups."""
    return _dsg_group_members(user, groups)


# handle OAuth2

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

def get_user_from_token(request: Request, token: str = Depends(oauth2_scheme)) -> User:
    """Validate the request token via DatasetGateway and return the User."""
    return _get_user_from_dsg(request, token)

def get_user(current_user: User = Depends(get_user_from_token)):
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


SERVICE_ACCOUNT_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _enforce_service_account_mutation(request: Request, token: str):
    resolved_token = _resolve_token(request, token)
    if not resolved_token:
        return

    current_user = _get_user_from_dsg(request, resolved_token)
    if current_user.service_account:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Service accounts are read-only in clio-store",
        )


async def enforce_service_account_read_only(
    request: Request, token: str = Depends(oauth2_scheme),
):
    """Reject every clio-store mutation made by a dedicated service account."""
    if request.method not in SERVICE_ACCOUNT_WRITE_METHODS:
        return
    await run_in_threadpool(_enforce_service_account_mutation, request, token)
