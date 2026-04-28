"""Tests for DatasetGateway (DSG) auth integration."""

import time
from unittest.mock import patch, MagicMock
from urllib.parse import urlencode

import httpx
import pytest
from starlette.requests import Request

from dependencies import (
    _map_dsg_to_user,
    _resolve_token,
    _get_user_from_dsg,
    _dsg_group_members,
    _dsg_user_cache,
    _dsg_group_members_cache,
    User,
    datasets,
    get_user,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(*, cookies=None, query_params=None, path="/"):
    """Build a minimal Starlette Request for unit testing."""
    headers = []
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers.append((b"cookie", cookie_str.encode()))
    qs = b""
    if query_params:
        qs = urlencode(query_params).encode()
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": qs,
        "headers": headers,
    }
    return Request(scope)


def _dsg_response(*, email="user@test.com", admin=False, permissions_v2=None,
                  permissions_v2_ignore_tos=None, missing_tos=None,
                  datasets_admin=None, groups=None, name="Test User"):
    """Build a DSG /api/v1/user/cache response dict."""
    return {
        "email": email,
        "name": name,
        "admin": admin,
        "permissions_v2": permissions_v2 or {},
        "permissions_v2_ignore_tos": permissions_v2_ignore_tos or {},
        "missing_tos": missing_tos or [],
        "datasets_admin": datasets_admin or [],
        "groups": groups or [],
    }


# ===========================================================================
# _map_dsg_to_user
# ===========================================================================

class TestMapDsgToUser:
    def test_admin_flag(self):
        user = _map_dsg_to_user(_dsg_response(admin=True))
        assert "admin" in user.global_roles

    def test_non_admin(self):
        user = _map_dsg_to_user(_dsg_response(admin=False))
        assert "admin" not in user.global_roles

    def test_view_permission_maps_to_clio_general(self):
        user = _map_dsg_to_user(_dsg_response(
            permissions_v2={"ds1": ["view"]},
        ))
        assert "clio_general" in user.datasets["ds1"]

    def test_ignore_tos_permissions_are_mapped_separately(self):
        user = _map_dsg_to_user(_dsg_response(
            permissions_v2={"accepted": ["view"]},
            permissions_v2_ignore_tos={"accepted": ["view"], "blocked": ["view"]},
            missing_tos=[{"dataset_name": "blocked", "tos_id": 12}],
        ))
        assert "accepted" in user.datasets
        assert "blocked" not in user.datasets
        assert "blocked" in user.datasets_ignore_tos
        assert user.missing_tos == [{"dataset_name": "blocked", "tos_id": 12}]

    def test_edit_permission_maps_to_clio_write(self):
        user = _map_dsg_to_user(_dsg_response(
            permissions_v2={"ds1": ["edit"]},
        ))
        assert "clio_write" in user.datasets["ds1"]

    def test_view_and_edit_combined(self):
        user = _map_dsg_to_user(_dsg_response(
            permissions_v2={"ds1": ["view", "edit"]},
        ))
        assert user.datasets["ds1"] == {"clio_general", "clio_write"}

    def test_datasets_admin_role(self):
        user = _map_dsg_to_user(_dsg_response(datasets_admin=["ds1"]))
        assert "dataset_admin" in user.datasets["ds1"]

    def test_datasets_admin_combined_with_view(self):
        user = _map_dsg_to_user(_dsg_response(
            permissions_v2={"ds1": ["view"]},
            datasets_admin=["ds1"],
        ))
        assert user.datasets["ds1"] == {"clio_general", "dataset_admin"}

    def test_groups_mapped(self):
        user = _map_dsg_to_user(_dsg_response(groups=["g1", "g2"]))
        assert user.groups == {"g1", "g2"}

    def test_multiple_datasets(self):
        user = _map_dsg_to_user(_dsg_response(
            permissions_v2={"ds1": ["view"], "ds2": ["edit"]},
        ))
        assert "clio_general" in user.datasets["ds1"]
        assert "clio_write" in user.datasets["ds2"]

    def test_missing_optional_fields(self):
        user = _map_dsg_to_user({"email": "a@b.com"})
        assert user.email == "a@b.com"
        assert user.global_roles == set()
        assert user.datasets == {}
        assert user.datasets_ignore_tos == {}
        assert user.missing_tos == []

    def test_unknown_permissions_ignored(self):
        user = _map_dsg_to_user(_dsg_response(
            permissions_v2={"ds1": ["unknown_perm"]},
        ))
        assert "ds1" not in user.datasets


# ===========================================================================
# _resolve_token
# ===========================================================================

class TestResolveToken:
    def test_bearer_token_has_priority(self):
        req = _make_request(
            cookies={"dsg_token": "cookie-tok"},
            query_params={"dsg_token": "query-tok"},
        )
        # token param simulates what OAuth2PasswordBearer extracts
        assert _resolve_token(req, "bearer-tok") == "bearer-tok"

    def test_cookie_fallback(self):
        req = _make_request(cookies={"dsg_token": "cookie-tok"})
        assert _resolve_token(req, None) == "cookie-tok"

    def test_query_param_fallback(self):
        req = _make_request(query_params={"dsg_token": "query-tok"})
        assert _resolve_token(req, None) == "query-tok"

    def test_no_token_returns_none(self):
        req = _make_request()
        assert _resolve_token(req, None) is None


# ===========================================================================
# _get_user_from_dsg
# ===========================================================================

class TestGetUserFromDsg:
    def test_successful_auth(self):
        req = _make_request()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _dsg_response(email="ok@test.com")

        with patch("dependencies.httpx.get", return_value=mock_resp):
            user = _get_user_from_dsg(req, "valid-token")
        assert user.email == "ok@test.com"

    def test_calls_service_aware_user_cache(self):
        req = _make_request()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _dsg_response(email="ok@test.com")

        with patch("dependencies.httpx.get", return_value=mock_resp) as mock_get:
            _get_user_from_dsg(req, "valid-token")
        assert mock_get.call_args.args[0] == "http://dsg.test/api/v1/user/cache?service=clio"

    def test_profile_path_bypasses_cached_user(self):
        req = _make_request(path="/profile")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _dsg_response(email="fresh@test.com")

        with patch("dependencies.httpx.get", return_value=mock_resp) as mock_get:
            _get_user_from_dsg(req, "profile-token")
            _get_user_from_dsg(req, "profile-token")
        assert mock_get.call_count == 2

    def test_no_token_raises_401(self):
        req = _make_request()
        with pytest.raises(Exception) as exc_info:
            _get_user_from_dsg(req, None)
        assert exc_info.value.status_code == 401

    def test_dsg_non_200_raises_401(self):
        req = _make_request()
        mock_resp = MagicMock()
        mock_resp.status_code = 403

        with patch("dependencies.httpx.get", return_value=mock_resp):
            with pytest.raises(Exception) as exc_info:
                _get_user_from_dsg(req, "bad-token")
            assert exc_info.value.status_code == 401

    def test_dsg_unreachable_raises_502(self):
        req = _make_request()
        with patch("dependencies.httpx.get", side_effect=httpx.ConnectError("fail")):
            with pytest.raises(Exception) as exc_info:
                _get_user_from_dsg(req, "tok")
            assert exc_info.value.status_code == 502

    def test_result_cached(self):
        req = _make_request()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _dsg_response(email="cached@test.com")

        with patch("dependencies.httpx.get", return_value=mock_resp) as mock_get:
            _get_user_from_dsg(req, "tok-cache")
            _get_user_from_dsg(req, "tok-cache")
            assert mock_get.call_count == 1

    def test_cache_expires_after_ttl(self):
        req = _make_request()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _dsg_response()

        with patch("dependencies.httpx.get", return_value=mock_resp) as mock_get:
            _get_user_from_dsg(req, "tok-expire")
            # Manually expire the cache entry
            _dsg_user_cache["tok-expire"] = (
                time.time() - 700,
                _dsg_user_cache["tok-expire"][1],
            )
            _get_user_from_dsg(req, "tok-expire")
            assert mock_get.call_count == 2


# ===========================================================================
# _dsg_group_members
# ===========================================================================

class TestDsgGroupMembers:
    def test_returns_member_emails(self):
        admin = User(email="a@test.com", name="A", global_roles={"admin"}, token="fake-token")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["m1@test.com", "m2@test.com"]

        with patch("dependencies.httpx.get", return_value=mock_resp) as mock_get:
            members = _dsg_group_members(admin, {"grp1"})
        assert members == {"m1@test.com", "m2@test.com"}
        mock_get.assert_called_once()
        assert mock_get.call_args.kwargs["headers"] == {"Authorization": "Bearer fake-token"}

    def test_non_admin_filtered_to_own_groups(self):
        user = User(email="u@test.com", name="U", groups={"grp1"}, token="fake-token")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["m@test.com"]

        with patch("dependencies.httpx.get", return_value=mock_resp) as mock_get:
            members = _dsg_group_members(user, {"grp1", "grp2"})
        # Only grp1 queried — user belongs to grp1 only
        assert mock_get.call_count == 1
        assert members == {"m@test.com"}

    def test_admin_can_query_any_group(self):
        admin = User(email="a@test.com", name="A", global_roles={"admin"}, token="fake-token")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["m@test.com"]

        with patch("dependencies.httpx.get", return_value=mock_resp) as mock_get:
            _dsg_group_members(admin, {"alien_grp"})
        assert mock_get.call_count == 1

    def test_results_cached(self):
        admin = User(email="a@test.com", name="A", global_roles={"admin"}, token="fake-token")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["m@test.com"]

        with patch("dependencies.httpx.get", return_value=mock_resp) as mock_get:
            _dsg_group_members(admin, {"grp1"})
            _dsg_group_members(admin, {"grp1"})
            assert mock_get.call_count == 1

    def test_connection_error_silently_skipped(self):
        admin = User(email="a@test.com", name="A", global_roles={"admin"}, token="fake-token")
        with patch("dependencies.httpx.get", side_effect=httpx.ConnectError("fail")):
            members = _dsg_group_members(admin, {"grp1"})
        assert members == set()

    def test_multiple_groups_merged(self):
        admin = User(email="a@test.com", name="A", global_roles={"admin"}, token="fake-token")

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "grp1" in url:
                resp.json.return_value = ["a@test.com"]
            else:
                resp.json.return_value = ["b@test.com"]
            return resp

        with patch("dependencies.httpx.get", side_effect=fake_get):
            members = _dsg_group_members(admin, {"grp1", "grp2"})
        assert members == {"a@test.com", "b@test.com"}


# ===========================================================================
# User model permissions
# ===========================================================================

class TestUserPermissions:
    def test_can_read_with_global_role(self):
        user = User(email="u@test.com", name="U", global_roles={"clio_general"})
        assert user.can_read("ds1")

    def test_can_read_public_dataset(self):
        datasets.public.add("pub_ds")
        user = User(email="u@test.com", name="U")
        assert user.can_read("pub_ds")

    def test_can_read_per_dataset_role(self):
        user = User(email="u@test.com", name="U", datasets={"ds1": {"clio_general"}})
        assert user.can_read("ds1")

    def test_can_read_ignore_tos_uses_granted_dataset_roles(self):
        user = User(
            email="u@test.com",
            name="U",
            datasets={},
            datasets_ignore_tos={"blocked": {"clio_general"}},
        )
        assert not user.can_read("blocked")
        assert user.can_read_ignore_tos("blocked")

    def test_cannot_read_without_role(self):
        user = User(email="u@test.com", name="U")
        assert not user.can_read("ds1")

    def test_can_write_own_public_dataset(self):
        datasets.public.add("pub_ds")
        user = User(email="u@test.com", name="U")
        assert user.can_write_own("pub_ds")

    def test_can_write_own_per_dataset(self):
        user = User(email="u@test.com", name="U", datasets={"ds1": {"clio_general"}})
        assert user.can_write_own("ds1")

    def test_can_write_others_requires_clio_write(self):
        user = User(email="u@test.com", name="U", datasets={"ds1": {"clio_write"}})
        assert user.can_write_others("ds1")
        assert not user.can_write_others("ds2")

    def test_is_dataset_admin_via_global_admin(self):
        user = User(email="u@test.com", name="U", global_roles={"admin"})
        assert user.is_dataset_admin("ds1")

    def test_is_dataset_admin_via_per_dataset(self):
        user = User(email="u@test.com", name="U", datasets={"ds1": {"dataset_admin"}})
        assert user.is_dataset_admin("ds1")
        assert not user.is_dataset_admin("ds2")


# ===========================================================================
# Endpoint tests (TestClient with dependency override)
# ===========================================================================

class TestDsgEndpoints:
    @pytest.fixture(autouse=True)
    def _setup_client(self, client, app):
        """Override get_user to bypass real auth for endpoint tests."""
        self._admin = User(
            email="admin@test.com", name="Admin", global_roles={"admin"},
        )
        app.dependency_overrides[get_user] = lambda: self._admin
        self.client = client
        yield
        app.dependency_overrides.clear()

    def test_get_users_returns_501(self):
        resp = self.client.get("/v2/users")
        assert resp.status_code == 501

    def test_post_users_returns_501(self):
        resp = self.client.post("/v2/users", json={"a@b.com": {}})
        assert resp.status_code == 501

    def test_delete_users_returns_501(self):
        resp = self.client.request("DELETE", "/v2/users", json=["a@b.com"])
        assert resp.status_code == 501

    def test_token_proxy_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"token": "new-tok"}

        with patch("services.server.httpx.post", return_value=mock_resp):
            resp = self.client.post(
                "/v2/server/token",
                headers={"Authorization": "Bearer orig-tok"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"token": "new-tok"}

    def test_token_proxy_connection_error(self):
        with patch("services.server.httpx.post", side_effect=httpx.ConnectError("fail")):
            resp = self.client.post(
                "/v2/server/token",
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 502


# ===========================================================================
# Auth routes (/login, /profile, /logout)
# ===========================================================================

class TestAuthRoutes:
    @pytest.fixture(autouse=True)
    def _setup_client(self, client, app):
        """Override get_user to bypass real auth for endpoint tests."""
        self._user = User(
            email="user@test.com", name="Test User",
            global_roles={"clio_general"},
            datasets={"ds1": {"clio_general", "clio_write"}},
            datasets_ignore_tos={"ds1": {"clio_general", "clio_write"}},
            groups={"grp1"},
        )
        app.dependency_overrides[get_user] = lambda: self._user
        self.client = client
        yield
        app.dependency_overrides.clear()

    def test_login_redirects_to_dsg(self):
        resp = self.client.get(
            "/login?redirect=https://clio.janelia.org/",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location.startswith("http://dsg.test/api/v1/authorize")
        assert "redirect=" in location
        assert "clio.janelia.org" in location
        assert "service=clio" in location

    def test_login_forwards_dataset_context_to_dsg(self):
        resp = self.client.get(
            "/login?redirect=https://clio.janelia.org/%3Fdataset%3Dfanc&dataset=fanc",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location.startswith("http://dsg.test/api/v1/authorize")
        assert "service=clio" in location
        assert "dataset=fanc" in location

    def test_login_requires_redirect_param(self):
        resp = self.client.get("/login")
        assert resp.status_code == 422  # FastAPI validation error

    def test_profile_returns_user_info(self):
        resp = self.client.get("/profile")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "user@test.com"
        assert data["name"] == "Test User"
        assert "clio_general" in data["global_roles"]
        assert "ds1" in data["datasets"]
        assert "clio_general" in data["datasets"]["ds1"]
        assert "clio_write" in data["datasets"]["ds1"]
        assert "ds1" in data["datasets_ignore_tos"]
        assert data["missing_tos"] == []
        assert "grp1" in data["groups"]

    def test_profile_empty_roles(self):
        from dependencies import app as _app
        empty_user = User(email="empty@test.com", name="Empty")
        _app.dependency_overrides[get_user] = lambda: empty_user
        resp = self.client.get("/profile")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "empty@test.com"
        assert data["global_roles"] == []
        assert data["datasets"] == {}
        assert data["datasets_ignore_tos"] == {}
        assert data["missing_tos"] == []
        assert data["groups"] == []

    def test_logout_redirects_to_requested_url(self):
        resp = self.client.post("/logout", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location == "/"


# ===========================================================================
# CORS credentials support
# ===========================================================================

class TestCorsCredentials:
    @pytest.fixture(autouse=True)
    def _setup_client(self, client, app):
        app.dependency_overrides[get_user] = lambda: User(
            email="u@test.com", name="U",
        )
        self.client = client
        yield
        app.dependency_overrides.clear()

    def test_cors_reflects_origin(self):
        resp = self.client.get(
            "/profile",
            headers={"Origin": "https://clio.janelia.org"},
        )
        assert resp.headers["access-control-allow-origin"] == "https://clio.janelia.org"
        assert resp.headers["access-control-allow-credentials"] == "true"

    def test_cors_preflight_reflects_origin(self):
        resp = self.client.options(
            "/profile",
            headers={"Origin": "https://clio.janelia.org"},
        )
        assert resp.headers["access-control-allow-origin"] == "https://clio.janelia.org"
        assert resp.headers["access-control-allow-credentials"] == "true"

    def test_cors_no_origin_returns_wildcard(self):
        resp = self.client.get("/profile")
        # No Origin header → falls back to "*" when ALLOWED_ORIGINS is "*"
        assert resp.headers["access-control-allow-origin"] in ("*", "")
