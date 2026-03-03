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

def _make_request(*, cookies=None, query_params=None):
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
        "path": "/",
        "query_string": qs,
        "headers": headers,
    }
    return Request(scope)


def _dsg_response(*, email="user@test.com", admin=False, permissions_v2=None,
                  datasets_admin=None, groups=None, name="Test User"):
    """Build a DSG /api/v1/user/cache response dict."""
    return {
        "email": email,
        "name": name,
        "admin": admin,
        "permissions_v2": permissions_v2 or {},
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
        admin = User(email="a@test.com", name="A", global_roles={"admin"})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["m1@test.com", "m2@test.com"]

        with patch("dependencies.httpx.get", return_value=mock_resp):
            members = _dsg_group_members(admin, {"grp1"})
        assert members == {"m1@test.com", "m2@test.com"}

    def test_non_admin_filtered_to_own_groups(self):
        user = User(email="u@test.com", name="U", groups={"grp1"})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["m@test.com"]

        with patch("dependencies.httpx.get", return_value=mock_resp) as mock_get:
            members = _dsg_group_members(user, {"grp1", "grp2"})
        # Only grp1 queried — user belongs to grp1 only
        assert mock_get.call_count == 1
        assert members == {"m@test.com"}

    def test_admin_can_query_any_group(self):
        admin = User(email="a@test.com", name="A", global_roles={"admin"})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["m@test.com"]

        with patch("dependencies.httpx.get", return_value=mock_resp) as mock_get:
            _dsg_group_members(admin, {"alien_grp"})
        assert mock_get.call_count == 1

    def test_results_cached(self):
        admin = User(email="a@test.com", name="A", global_roles={"admin"})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["m@test.com"]

        with patch("dependencies.httpx.get", return_value=mock_resp) as mock_get:
            _dsg_group_members(admin, {"grp1"})
            _dsg_group_members(admin, {"grp1"})
            assert mock_get.call_count == 1

    def test_connection_error_silently_skipped(self):
        admin = User(email="a@test.com", name="A", global_roles={"admin"})
        with patch("dependencies.httpx.get", side_effect=httpx.ConnectError("fail")):
            members = _dsg_group_members(admin, {"grp1"})
        assert members == set()

    def test_multiple_groups_merged(self):
        admin = User(email="a@test.com", name="A", global_roles={"admin"})

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
