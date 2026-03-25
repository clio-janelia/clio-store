"""Browser-facing auth routes for DatasetGateway integration.

These routes handle the OAuth redirect dance: /login redirects to DSG,
/profile returns user info from the DSG-authenticated session, and
/logout redirects to DSG's logout endpoint.

Only registered when DSG_URL is set (see main.py).
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from config import DSG_URL
from dependencies import get_user, User

router = APIRouter()


@router.get('/login')
async def login(redirect: str, request: Request):
    """Redirect to DatasetGateway's OAuth authorize endpoint."""
    if not DSG_URL:
        raise HTTPException(status_code=404)
    target = f"{DSG_URL}/api/v1/authorize?redirect={quote(redirect, safe='')}"
    return RedirectResponse(target, status_code=302)


@router.get('/profile')
async def profile(user: User = Depends(get_user)):
    """Return the authenticated user's identity and permissions."""
    return {
        "email": user.email,
        "name": user.name,
        "global_roles": list(user.global_roles or []),
        "datasets": {ds: list(roles) for ds, roles in (user.datasets or {}).items()},
        "groups": list(user.groups or []),
    }


@router.post('/logout')
async def logout():
    """Redirect to DatasetGateway's logout endpoint to clear the dsg_token cookie."""
    if not DSG_URL:
        raise HTTPException(status_code=404)
    return RedirectResponse(f"{DSG_URL}/api/v1/logout", status_code=302)
