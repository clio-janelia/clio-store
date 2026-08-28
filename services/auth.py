"""Browser-facing auth routes for DatasetGateway integration.

These routes handle the OAuth redirect dance: /login redirects to DSG,
/profile returns user info from the DSG-authenticated session, and
/logout invalidates the session and returns the browser to a chosen URL.

Only registered when DSG_URL is set (see main.py).
"""

from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from config import DSG_URL, OWNER
from dependencies import (
    User,
    _dsg_entry_for_dataset_id,
    _fetch_dsg_decisions,
    _fetch_dsg_identity,
    _map_dsg_roles_to_clio_roles,
    _resolve_token,
    get_user,
    oauth2_scheme,
)

router = APIRouter()


@router.get('/login')
async def login(
    redirect: str,
):
    """Redirect to DatasetGateway's OAuth authorize endpoint."""
    if not DSG_URL:
        raise HTTPException(status_code=404)
    target = f"{DSG_URL}/api/v1/authorize?{urlencode({'redirect': redirect})}"
    return RedirectResponse(target, status_code=302)


@router.get('/profile')
async def profile(user: User = Depends(get_user)):
    """Return the authenticated user's identity and permissions."""
    return {
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "global_roles": list(user.global_roles or []),
        "datasets": {ds: list(roles) for ds, roles in (user.datasets or {}).items()},
        "groups": list(user.groups or []),
        # Tell the frontend we're in DSG mode and where user admin lives.
        # Frontend uses this to link out to DSG's admin UI for user management
        # (clio-store's /v2/users returns 501 in DSG mode).
        "dsg_url": DSG_URL or None,
    }


@router.get('/dataset-access')
async def dataset_access(
    request: Request,
    dataset: str,
    redirect: str,
    token: Optional[str] = Depends(oauth2_scheme),
):
    """Return one fresh, opaque DSG authorization decision for the browser."""
    resolved_token = _resolve_token(request, token)
    if not resolved_token:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    identity = _fetch_dsg_identity(resolved_token)
    is_admin = identity.get("admin") or (
        not identity.get("service_account")
        and OWNER
        and identity.get("email") == OWNER
    )
    if is_admin:
        return {
            "dataset": dataset,
            "access": True,
            "tos_required": False,
            "roles": sorted(_map_dsg_roles_to_clio_roles(["view", "edit", "admin"])),
        }

    decision = _fetch_dsg_decisions(
        resolved_token, [_dsg_entry_for_dataset_id(dataset)], return_url=redirect,
    )[0]
    decision_type = decision.get("decision")
    result = {
        "dataset": dataset,
        "access": decision_type == "allow",
        "tos_required": decision_type == "tos_required",
        "roles": sorted(_map_dsg_roles_to_clio_roles(decision.get("roles"))),
    }
    if decision_type == "tos_required" and "tos_url" in decision:
        result["tos_url"] = decision["tos_url"]
    return result


@router.get('/logout')
@router.post('/logout')
async def logout(redirect: str = "/"):
    """Delegate browser logout and redirect validation to DatasetGateway.

    DSG deletes the cookie-presented login token, clears the domain-wide
    dsg_token cookie, validates the return URL, and redirects the browser.
    """
    if not DSG_URL:
        raise HTTPException(status_code=404)

    target = f"{DSG_URL}/api/v1/logout?{urlencode({'redirect': redirect})}"
    return RedirectResponse(target, status_code=302)
