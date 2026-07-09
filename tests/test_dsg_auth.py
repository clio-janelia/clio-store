"""Tests for clio-store's DSG-native identity and authorization adapter."""

import asyncio
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import MagicMock, patch

import httpx
import pytest
from starlette.requests import Request

from dependencies import (
    User,
    _build_user,
    _dsg_entry_for_dataset_id,
    _dsg_group_members,
    _dsg_group_members_cache,
    _dsg_user_cache,
    _evict_other_user_tokens,
    _fetch_dsg_decisions,
    _fetch_dsg_identity,
    _fetch_dsg_user,
    _get_user_from_dsg,
    _map_dsg_roles_to_clio_roles,
    _resolve_token,
    datasets,
    get_user,
    refresh_user,
)
from services.auth import dataset_access, login, logout, profile
from services.datasets import get_dataset as get_dataset_route


def _make_request(*, cookies=None, query_params=None, path="/"):
    headers = []
    if cookies:
        headers.append((b"cookie", "; ".join(
            f"{key}={value}" for key, value in cookies.items()
        ).encode()))
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": urlencode(query_params or {}).encode(),
        "headers": headers,
    })


def _response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


def _identity(*, email="user@test.com", admin=False, groups=None, name="Test User"):
    return {
        "id": 1,
        "email": email,
        "name": name,
        "picture_url": "https://example.test/avatar.png",
        "admin": admin,
        "service_account": False,
        "groups": groups or [],
    }


def _decision(entry, decision="allow", roles=None, **extra):
    result = {key: entry[key] for key in ("name", "version") if key in entry}
    result.update({"decision": decision, "roles": roles or []})
    result.update(extra)
    return result


@pytest.fixture(autouse=True)
def _reset_dataset_cache():
    cache = datasets.cache.copy()
    public = datasets.public.copy()
    datasets.cache.clear()
    datasets.public.clear()
    yield
    datasets.cache = cache
    datasets.public = public


# ---------------------------------------------------------------------------
# Entry and role mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("dataset_id", "entry"), [
    ("fish2:v0.6", {"name": "fish2", "version": "v0.6", "permission": "view"}),
    ("a:b:c", {"name": "a", "version": "b:c", "permission": "view"}),
    ("a:", {"name": "a:", "permission": "view"}),
    (":v1", {"name": ":v1", "permission": "view"}),
    (":", {"name": ":", "permission": "view"}),
    ("a::b", {"name": "a", "version": ":b", "permission": "view"}),
    ("a:0", {"name": "a", "version": "0", "permission": "view"}),
    ("dataset:release:candidate", {
        "name": "dataset", "version": "release:candidate", "permission": "view",
    }),
    ("plain-name", {"name": "plain-name", "permission": "view"}),
    ("bare", {"name": "bare", "permission": "view"}),
])
def test_dataset_id_split_rule(dataset_id, entry):
    assert _dsg_entry_for_dataset_id(dataset_id) == entry


@pytest.mark.parametrize(("native_roles", "clio_roles"), [
    (["view"], {"clio_general"}),
    (["edit"], {"clio_write"}),
    (["admin"], {"dataset_admin"}),
    (["manage"], set()),
    (["view", "edit", "admin", "manage", "annotation_editor"], {
        "clio_general", "clio_write", "dataset_admin", "annotation_editor",
    }),
    ([], set()),
    (["annotation_editor"], {"annotation_editor"}),
    (["view", "manage"], {"clio_general"}),
    (["edit", "manage"], {"clio_write"}),
    (["admin", "manage"], {"dataset_admin"}),
    (["view", "edit"], {"clio_general", "clio_write"}),
    (["view", "edit", "admin"], {"clio_general", "clio_write", "dataset_admin"}),
])
def test_native_role_mapping(native_roles, clio_roles):
    assert _map_dsg_roles_to_clio_roles(native_roles) == clio_roles


def test_build_user_maps_allow_and_pending_tos_without_storing_tos_url():
    entries = [_dsg_entry_for_dataset_id("accepted"), _dsg_entry_for_dataset_id("waiting")]
    user = _build_user(_identity(groups=["lab"]), ["accepted", "waiting"], [
        _decision(entries[0], roles=["view", "edit"]),
        _decision(
            entries[1], decision="tos_required", roles=["view"],
            tos_url="https://dsg.test/opaque-tos",
        ),
    ])

    assert user.datasets == {"accepted": {"clio_general", "clio_write"}}
    assert user.datasets_ignore_tos == {
        "accepted": {"clio_general", "clio_write"},
        "waiting": {"clio_general"},
    }
    assert user.groups == {"lab"}
    assert "tos_url" not in user.dict()


def test_build_user_ignores_deny_and_service_eval(capsys):
    entries = [_dsg_entry_for_dataset_id("denied"), _dsg_entry_for_dataset_id("dag")]
    user = _build_user(_identity(), ["denied", "dag"], [
        _decision(entries[0], decision="deny"),
        _decision(entries[1], decision="service_eval", roles=["view"]),
    ])

    assert user.datasets == {}
    assert user.datasets_ignore_tos == {}
    assert "requires service evaluation for dataset dag" in capsys.readouterr().out


def test_build_user_honors_dsg_admin_and_owner_fallback():
    entry = _dsg_entry_for_dataset_id("ds")
    assert "admin" in _build_user(
        _identity(admin=True), ["ds"], [_decision(entry)]
    ).global_roles
    assert "admin" in _build_user(
        _identity(email="owner@test.com"), ["ds"], [_decision(entry)]
    ).global_roles


# ---------------------------------------------------------------------------
# Native DSG requests
# ---------------------------------------------------------------------------


def test_fetch_identity_uses_native_url_and_bearer_token():
    with patch("dependencies.httpx.get", return_value=_response(_identity())) as get:
        identity = _fetch_dsg_identity("token-1")

    assert identity["email"] == "user@test.com"
    assert get.call_args.args[0] == "http://dsg.test/api/dsg/v1/user"
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer token-1"}


def test_fetch_identity_rejects_dedicated_service_account():
    identity = _identity(email=None)
    identity["service_account"] = True
    with patch("dependencies.httpx.get", return_value=_response(identity)):
        with pytest.raises(Exception) as error:
            _fetch_dsg_identity("dedicated")
    assert error.value.status_code == 401
    assert "Dedicated service accounts" in error.value.detail


def test_fetch_identity_maps_non_ok_to_401():
    with patch("dependencies.httpx.get", return_value=_response({}, status_code=403)):
        with pytest.raises(Exception) as error:
            _fetch_dsg_identity("bad")
    assert error.value.status_code == 401


def test_fetch_identity_maps_request_failure_to_502():
    with patch("dependencies.httpx.get", side_effect=httpx.ConnectError("nope")):
        with pytest.raises(Exception) as error:
            _fetch_dsg_identity("offline")
    assert error.value.status_code == 502


def test_fetch_decisions_posts_one_ordered_native_batch_and_correlates():
    entries = [_dsg_entry_for_dataset_id("fish2:v0.6"), _dsg_entry_for_dataset_id("other")]
    decisions = [
        _decision(entries[0], roles=["view"]),
        _decision(entries[1], decision="deny"),
    ]
    with patch("dependencies.httpx.post", return_value=_response({"entries": decisions})) as post:
        assert _fetch_dsg_decisions("token", entries) == decisions

    assert post.call_args.args[0] == "http://dsg.test/api/dsg/v1/authorize"
    assert post.call_args.kwargs["json"] == {"service": "clio", "entries": entries}


def test_fetch_decisions_sends_return_url_only_for_browser_check():
    entry = _dsg_entry_for_dataset_id("fish2:v0.6")
    with patch("dependencies.httpx.post", return_value=_response({"entries": [_decision(entry)]})) as post:
        _fetch_dsg_decisions("token", [entry], return_url="https://clio.test/return")

    assert post.call_args.kwargs["json"]["return_url"] == "https://clio.test/return"


@pytest.mark.parametrize("body", [
    {"entries": []},
    {"entries": [{"name": "wrong", "version": "v0.6", "decision": "allow", "roles": []}]},
    {"entries": "not-a-list"},
])
def test_fetch_decisions_rejects_bad_correlation_as_502(body):
    entry = _dsg_entry_for_dataset_id("fish2:v0.6")
    with patch("dependencies.httpx.post", return_value=_response(body)):
        with pytest.raises(Exception) as error:
            _fetch_dsg_decisions("token", [entry])
    assert error.value.status_code == 502


def test_fetch_decisions_maps_request_and_http_errors():
    entry = _dsg_entry_for_dataset_id("fish2")
    with patch("dependencies.httpx.post", side_effect=httpx.ConnectError("nope")):
        with pytest.raises(Exception) as error:
            _fetch_dsg_decisions("token", [entry])
    assert error.value.status_code == 502

    with patch("dependencies.httpx.post", return_value=_response({}, status_code=401)):
        with pytest.raises(Exception) as error:
            _fetch_dsg_decisions("token", [entry])
    assert error.value.status_code == 401


# ---------------------------------------------------------------------------
# Cached User lifecycle
# ---------------------------------------------------------------------------


def _native_fetches(identity, entries, decisions):
    return (
        patch("dependencies.httpx.get", return_value=_response(identity)),
        patch("dependencies.httpx.post", return_value=_response({"entries": decisions})),
    )


def test_fetch_user_batches_all_firestore_dataset_ids_and_fixes_colon_regression():
    datasets.cache.update({"fish2:v0.6": MagicMock(), "bare": MagicMock()})
    entries = [_dsg_entry_for_dataset_id(dataset_id) for dataset_id in datasets.cache]
    decisions = [_decision(entries[0], roles=["view"]), _decision(entries[1], decision="deny")]
    get, post = _native_fetches(_identity(), entries, decisions)
    with get, post:
        user = _fetch_dsg_user("token")

    assert user.can_read("fish2:v0.6")
    assert not user.can_read("bare")
    assert _dsg_user_cache["token"][1] is user


def test_get_user_caches_for_ttl_and_profile_forces_fresh():
    datasets.cache["ds"] = MagicMock()
    entry = _dsg_entry_for_dataset_id("ds")
    decisions = [_decision(entry, roles=["view"])]
    get, post = _native_fetches(_identity(), [entry], decisions)
    with get as mock_get, post as mock_post:
        _get_user_from_dsg(_make_request(path="/v2/datasets"), "token")
        _get_user_from_dsg(_make_request(path="/v2/datasets"), "token")
        _get_user_from_dsg(_make_request(path="/profile"), "token")
    assert mock_get.call_count == 2
    assert mock_post.call_count == 2


def test_expired_cache_refetches():
    datasets.cache["ds"] = MagicMock()
    entry = _dsg_entry_for_dataset_id("ds")
    get, post = _native_fetches(_identity(), [entry], [_decision(entry)])
    with get as mock_get, post:
        _get_user_from_dsg(_make_request(), "token")
        _dsg_user_cache["token"] = (time.time() - 601, _dsg_user_cache["token"][1])
        _get_user_from_dsg(_make_request(), "token")
    assert mock_get.call_count == 2


def test_no_token_is_401():
    with pytest.raises(Exception) as error:
        _get_user_from_dsg(_make_request(), None)
    assert error.value.status_code == 401


def test_profile_refresh_evicts_sibling_tokens_but_not_other_users():
    sibling = User(email="same@test.com", name="Same", token="sibling")
    other = User(email="other@test.com", name="Other", token="other")
    _dsg_user_cache.update({
        "sibling": (time.time(), sibling),
        "other": (time.time(), other),
    })
    get, post = _native_fetches(_identity(email="same@test.com"), [], [])
    with get, post:
        _get_user_from_dsg(_make_request(path="/profile"), "cookie")
    assert "cookie" in _dsg_user_cache
    assert "sibling" not in _dsg_user_cache
    assert "other" in _dsg_user_cache


def test_refresh_user_rereads_after_data_route_denial():
    datasets.cache["fish2:v0.6"] = MagicMock()
    stale = User(email="user@test.com", name="Test", token="token")
    _dsg_user_cache["token"] = (time.time(), stale)
    entry = _dsg_entry_for_dataset_id("fish2:v0.6")
    get, post = _native_fetches(_identity(), [entry], [_decision(entry, roles=["view"])])
    with get, post:
        fresh = refresh_user(stale)
    assert fresh.can_read("fish2:v0.6")
    assert _dsg_user_cache["token"][1] is fresh


def test_refresh_user_without_token_or_on_failure_keeps_original():
    no_token = User(email="user@test.com", name="Test")
    assert refresh_user(no_token) is no_token

    failing = User(email="user@test.com", name="Test", token="token")
    with patch("dependencies.httpx.get", side_effect=httpx.ConnectError("nope")):
        assert refresh_user(failing) is failing


def test_post_acceptance_profile_refresh_flips_same_and_sibling_token_without_ttl_wait():
    datasets.cache["waiting"] = MagicMock()
    entry = _dsg_entry_for_dataset_id("waiting")
    pending = _decision(entry, decision="tos_required", roles=["view"])
    allowed = _decision(entry, decision="allow", roles=["view"])
    get = patch("dependencies.httpx.get", return_value=_response(_identity()))
    post = patch(
        "dependencies.httpx.post",
        side_effect=[
            _response({"entries": [pending]}),
            _response({"entries": [pending]}),
            _response({"entries": [allowed]}),
            _response({"entries": [allowed]}),
        ],
    )
    document = MagicMock()
    document.exists = True
    document.to_dict.return_value = {"title": "Waiting dataset"}
    collection = MagicMock()
    collection.document.return_value.get.return_value = document
    with get, post, patch("services.datasets.firestore.get_collection", return_value=collection):
        same = _get_user_from_dsg(_make_request(path="/v2/datasets/waiting"), "same")
        sibling = _get_user_from_dsg(_make_request(path="/v2/datasets/waiting"), "sibling")
        assert get_dataset_route("waiting", current_user=same) is None
        assert get_dataset_route("waiting", current_user=sibling) is None

        refreshed = _get_user_from_dsg(_make_request(path="/profile"), "same")
        assert get_dataset_route("waiting", current_user=refreshed) == {"title": "Waiting dataset"}
        assert "sibling" not in _dsg_user_cache

        sibling_after = _get_user_from_dsg(_make_request(path="/v2/datasets/waiting"), "sibling")
        assert get_dataset_route("waiting", current_user=sibling_after) == {"title": "Waiting dataset"}


def test_evict_other_tokens_matches_email():
    _dsg_user_cache.update({
        "keep": (time.time(), User(email="same@test.com", name="Same")),
        "drop": (time.time(), User(email="same@test.com", name="Same")),
        "other": (time.time(), User(email="other@test.com", name="Other")),
    })
    _evict_other_user_tokens("same@test.com", "keep")
    assert set(_dsg_user_cache) == {"keep", "other"}


# ---------------------------------------------------------------------------
# User permissions and group membership
# ---------------------------------------------------------------------------


def test_admin_short_circuits_every_authorization_method_for_firestore_only_dataset():
    user = User(email="admin@test.com", name="Admin", global_roles={"admin"})
    assert user.has_role("anything", "firestore-only")
    assert user.can_read("firestore-only")
    assert user.can_read_ignore_tos("firestore-only")
    assert user.can_write_own("firestore-only")
    assert user.can_write_others("firestore-only")
    assert user.is_dataset_admin("firestore-only")


def test_non_admin_user_permissions_and_public_or_source():
    datasets.public.add("local-public")
    user = User(
        email="user@test.com",
        name="User",
        datasets={"view": {"clio_general"}, "write": {"clio_write"}},
        datasets_ignore_tos={"waiting": {"clio_general"}},
    )
    assert user.can_read("local-public")
    assert user.can_read("view")
    assert user.can_write_own("write")
    assert user.can_write_others("write")
    assert not user.can_read("waiting")
    assert user.can_read_ignore_tos("waiting")


def test_group_members_uses_native_url_and_keeps_non_admin_filter():
    user = User(email="user@test.com", name="User", groups={"mine"}, token="token")
    with patch("dependencies.httpx.get", return_value=_response(["member@test.com"])) as get:
        assert _dsg_group_members(user, {"mine", "not-mine"}) == {"member@test.com"}
    assert get.call_args.args[0] == "http://dsg.test/api/dsg/v1/groups/mine/members"


def test_group_members_allows_admin_and_caches_result():
    user = User(email="admin@test.com", name="Admin", global_roles={"admin"}, token="token")
    with patch("dependencies.httpx.get", return_value=_response(["member@test.com"])) as get:
        assert _dsg_group_members(user, {"any"}) == {"member@test.com"}
        assert _dsg_group_members(user, {"any"}) == {"member@test.com"}
    assert get.call_count == 1
    assert "any" in _dsg_group_members_cache


def test_group_members_ignores_request_failures():
    user = User(email="admin@test.com", name="Admin", global_roles={"admin"}, token="token")
    with patch("dependencies.httpx.get", side_effect=httpx.ConnectError("nope")):
        assert _dsg_group_members(user, {"any"}) == set()


@pytest.mark.parametrize(("token", "cookies", "query", "expected"), [
    ("bearer", {"dsg_token": "cookie"}, {"dsg_token": "query"}, "bearer"),
    (None, {"dsg_token": "cookie"}, {}, "cookie"),
    (None, {}, {"dsg_token": "query"}, "query"),
    (None, {}, {}, None),
])
def test_token_resolution_order(token, cookies, query, expected):
    assert _resolve_token(_make_request(cookies=cookies, query_params=query), token) == expected


# ---------------------------------------------------------------------------
# Browser-facing route functions
# ---------------------------------------------------------------------------


def test_login_forwards_only_redirect_and_ignores_stale_dataset():
    redirect = "https://clio.test/?dataset=fish2:v0.6"
    response = asyncio.run(login(redirect=redirect))
    target = urlsplit(response.headers["location"])
    assert response.status_code == 302
    assert target.path == "/api/v1/authorize"
    assert parse_qs(target.query) == {"redirect": [redirect]}


def test_profile_has_only_native_frontend_shape():
    user = User(
        email="user@test.com",
        name="Test User",
        picture="https://example.test/avatar.png",
        global_roles={"clio_general"},
        datasets={"ds": {"clio_general"}},
        groups={"lab"},
    )
    response = asyncio.run(profile(user))
    assert set(response) == {
        "email", "name", "picture", "global_roles", "datasets", "groups", "dsg_url",
    }
    assert response["groups"] == ["lab"]


def test_dataset_access_allow_is_fresh_and_does_not_write_user_cache():
    entry = _dsg_entry_for_dataset_id("fish2:v0.6")
    request = _make_request(cookies={"dsg_token": "cookie"})
    with patch("dependencies.httpx.get", return_value=_response(_identity())), patch(
        "dependencies.httpx.post",
        return_value=_response({"entries": [_decision(entry, roles=["view"])]}),
    ) as post:
        response = asyncio.run(dataset_access(
            request, "fish2:v0.6", "https://clio.test/return", None,
        ))
    assert response == {
        "dataset": "fish2:v0.6", "access": True, "tos_required": False,
        "roles": ["clio_general"],
    }
    assert _dsg_user_cache == {}
    assert post.call_args.kwargs["json"]["return_url"] == "https://clio.test/return"


def test_dataset_access_tos_url_is_opaque_and_fresh_each_call():
    entry = _dsg_entry_for_dataset_id("waiting")
    decision = _decision(
        entry, decision="tos_required", roles=["view"],
        tos_url="https://dsg.test/opaque?token=never-parse",
    )
    request = _make_request(cookies={"dsg_token": "cookie"})
    with patch("dependencies.httpx.get", return_value=_response(_identity())) as get, patch(
        "dependencies.httpx.post", return_value=_response({"entries": [decision]})
    ) as post:
        first = asyncio.run(dataset_access(request, "waiting", "https://clio.test/", None))
        second = asyncio.run(dataset_access(request, "waiting", "https://clio.test/", None))
    assert first["tos_url"] == "https://dsg.test/opaque?token=never-parse"
    assert first["access"] is False
    assert first["tos_required"] is True
    assert get.call_count == 2
    assert post.call_count == 2


def test_dataset_access_admin_never_calls_authorize():
    request = _make_request(cookies={"dsg_token": "cookie"})
    with patch("dependencies.httpx.get", return_value=_response(_identity(admin=True))), patch(
        "dependencies.httpx.post"
    ) as post:
        response = asyncio.run(dataset_access(
            request, "firestore-only", "https://clio.test/", None,
        ))
    assert response["access"] is True
    assert response["tos_required"] is False
    post.assert_not_called()


def test_dataset_access_owner_never_calls_authorize():
    request = _make_request(cookies={"dsg_token": "cookie"})
    with patch("dependencies.httpx.get", return_value=_response(_identity(email="owner@test.com"))), patch(
        "dependencies.httpx.post"
    ) as post:
        response = asyncio.run(dataset_access(
            request, "firestore-only", "https://clio.test/", None,
        ))
    assert response["access"] is True
    post.assert_not_called()


def test_dataset_access_requires_cookie_or_bearer():
    with pytest.raises(Exception) as error:
        asyncio.run(dataset_access(_make_request(), "ds", "https://clio.test/", None))
    assert error.value.status_code == 401


def test_browser_routes_are_mounted_and_dataset_access_uses_cookie_credentials():
    import main  # noqa: F401 -- wire the router into the shared FastAPI app
    from dependencies import app

    entry = _dsg_entry_for_dataset_id("fish2:v0.6")

    async def request_routes():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            login_response = await client.get(
                "/login?redirect=https%3A%2F%2Fclio.test%2F&dataset=stale&service=stale",
            )
            access_response = await client.get(
                "/dataset-access?dataset=fish2%3Av0.6&redirect=https%3A%2F%2Fclio.test%2F",
                headers={"Cookie": "dsg_token=cookie", "Origin": "https://clio.test"},
            )
            return login_response, access_response

    with patch("dependencies.httpx.get", return_value=_response(_identity())), patch(
        "dependencies.httpx.post",
        return_value=_response({"entries": [_decision(entry, roles=["view"])]}),
    ):
        login_response, access_response = asyncio.run(request_routes())

    assert parse_qs(urlsplit(login_response.headers["location"]).query) == {
        "redirect": ["https://clio.test/"],
    }
    assert access_response.json()["access"] is True
    assert access_response.headers["access-control-allow-origin"] == "https://clio.test"
    assert access_response.headers["access-control-allow-credentials"] == "true"


def test_logout_redirects():
    response = asyncio.run(logout(_make_request()))
    assert response.status_code == 302
    assert response.headers["location"] == "/"
