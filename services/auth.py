"""Browser-facing auth routes for DatasetGateway integration.

These routes handle the OAuth redirect dance: /login redirects to DSG,
/profile returns user info from the DSG-authenticated session, and
/logout invalidates the session and returns the browser to a chosen URL.

Only registered when DSG_URL is set (see main.py).
"""

from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from config import DSG_URL
from dependencies import get_user, User

router = APIRouter()


@router.get('/login')
async def login(
    redirect: str,
    request: Request,
    dataset: Optional[str] = None,
    service: str = "clio",
):
    """Redirect to DatasetGateway's OAuth authorize endpoint."""
    if not DSG_URL:
        raise HTTPException(status_code=404)
    params = {"redirect": redirect}
    if service:
        params["service"] = service
    if dataset:
        params["dataset"] = dataset
    target = f"{DSG_URL}/api/v1/authorize?{urlencode(params)}"
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
        "datasets_ignore_tos": {
            ds: list(roles) for ds, roles in (user.datasets_ignore_tos or {}).items()
        },
        "missing_tos": list(user.missing_tos or []),
        "groups": list(user.groups or []),
        # Tell the frontend we're in DSG mode and where user admin lives.
        # Frontend uses this to link out to DSG's admin UI for user management
        # (clio-store's /v2/users returns 501 in DSG mode).
        "dsg_url": DSG_URL or None,
    }


@router.get('/logout')
@router.post('/logout')
async def logout(request: Request, redirect: str = "/"):
    """Invalidate the DSG session and redirect the browser.

    DSG's /api/v1/logout returns a JSON blob rather than honoring a redirect,
    which makes for ugly UX if we just 302 the browser there. Instead we:
      1. best-effort call DSG /api/v1/logout server-side with the user's token
         so the APIKey row is deleted from DSG's DB,
      2. clear the dsg_token cookie ourselves (valid because the cookie is
         Domain=.janelia.org and we're a .janelia.org subdomain),
      3. redirect the browser to whatever URL the caller asked for.
    """
    if not DSG_URL:
        raise HTTPException(status_code=404)

    token = request.cookies.get("dsg_token")
    if token:
        try:
            httpx.get(
                f"{DSG_URL}/api/v1/logout",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
        except httpx.RequestError:
            # best-effort — we still want to clear the local cookie and redirect
            pass

    response = RedirectResponse(redirect, status_code=302)
    # DSG sets the cookie with Domain=.janelia.org; we must echo the same
    # domain when clearing or the browser keeps the cookie.
    response.delete_cookie("dsg_token", domain=".janelia.org", path="/")
    return response
