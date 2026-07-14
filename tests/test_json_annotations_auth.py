"""Authorization tests for DVID-backed JSON annotation routes."""

import asyncio
from unittest.mock import MagicMock, call, patch

import pytest
import requests
from fastapi import HTTPException
from starlette.requests import Request

from dependencies import User
from services import json_annotations as annotations


def _request(origin=None):
    headers = []
    if origin:
        headers.append((b"origin", origin.encode()))
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/v2/json-annotations/ds/neurons/fields",
        "query_string": b"",
        "headers": headers,
    })


def _user(*, token="dsg-token", roles=None, datasets=None):
    return User(
        email="user@test.com",
        name="Test User",
        token=token,
        global_roles=set(roles or []),
        datasets=datasets or {},
    )


def _dataset(*, dvid="https://dvid.test/", uuid="abc123"):
    dataset = MagicMock()
    dataset.dvid = dvid
    dataset.uuid = uuid
    return dataset


def _response(body=None, *, status_code=200, content=b""):
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    response.json.return_value = body
    return response


def _assert_http_error(error, status_code):
    assert isinstance(error.value, HTTPException)
    assert error.value.status_code == status_code


def test_resolve_dvid_target_supports_head_tag_and_bare_uuid():
    with patch.object(annotations, "get_dataset", return_value=_dataset()), patch.object(
        annotations.cache, "get_value", return_value={"v1.2.3": "def456"},
    ):
        head = annotations.resolve_dvid_target("ds")
        tagged = annotations.resolve_dvid_target("ds", "v1.2.3")
        bare = annotations.resolve_dvid_target("ds", "A0b9")

    assert head == annotations.DVIDTarget("https://dvid.test", "abc123")
    assert tagged == annotations.DVIDTarget("https://dvid.test", "def456")
    assert bare == annotations.DVIDTarget("https://dvid.test", "A0b9")


@pytest.mark.parametrize("version", ["not-a-uuid", "123/escape", "", None])
def test_resolve_dvid_target_rejects_non_hex_nodes(version):
    dataset = _dataset(uuid=None if version == "" else "abc123")
    with patch.object(annotations, "get_dataset", return_value=dataset):
        with pytest.raises(HTTPException) as error:
            annotations.resolve_dvid_target("ds", version)
    _assert_http_error(error, 400)


def test_broker_posts_bearer_to_resolved_node_and_returns_capability():
    target = annotations.DVIDTarget("https://dvid.test", "abc123")
    broker_response = _response({
        "decision": "allow",
        "roles": ["view", "named-role"],
        "capability": "opaque-capability",
    })
    with patch.object(annotations, "ALLOWED_ORIGINS", "https://clio.test,https://other.test"), patch.object(
        annotations.requests, "post", return_value=broker_response,
    ) as post:
        capability = annotations.dvid_capability(
            target, _user(), "view", _request("https://clio.test"),
        )

    assert capability == "opaque-capability"
    post.assert_called_once_with(
        "https://dvid.test/api/auth/clio/abc123",
        json={"permission": "view", "return_url": "https://clio.test"},
        headers={"Authorization": "Bearer dsg-token"},
        timeout=10,
    )


@pytest.mark.parametrize(
    ("allowed_origins", "origin"),
    [("*", "https://clio.test"), ("https://clio.test", "https://evil.test"),
     ("https://clio.test", None)],
)
def test_broker_omits_untrusted_or_wildcard_return_url(allowed_origins, origin):
    with patch.object(annotations, "ALLOWED_ORIGINS", allowed_origins), patch.object(
        annotations.requests,
        "post",
        return_value=_response({"decision": "allow", "capability": "opaque"}),
    ) as post:
        annotations.dvid_capability(
            annotations.DVIDTarget("https://dvid.test", "abc123"),
            _user(roles={"admin"}),
            "edit",
            _request(origin),
        )

    assert post.call_args.kwargs["json"] == {"permission": "edit"}


@pytest.mark.parametrize(
    ("body", "status_code"),
    [
        ({"decision": "deny"}, 403),
        ({"decision": "allow"}, 502),
        ({"decision": "tos_required"}, 502),
        ({"decision": "unknown"}, 502),
        ([], 502),
    ],
)
def test_broker_decisions_fail_closed_without_capability(body, status_code):
    with patch.object(annotations.requests, "post", return_value=_response(body)):
        with pytest.raises(HTTPException) as error:
            annotations.dvid_capability(
                annotations.DVIDTarget("https://dvid.test", "abc123"),
                _user(),
                "view",
                _request(),
            )
    _assert_http_error(error, status_code)


def test_broker_tos_returns_only_opaque_url():
    body = {"decision": "tos_required", "tos_url": "https://dsg.test/opaque"}
    with patch.object(annotations.requests, "post", return_value=_response(body)):
        with pytest.raises(HTTPException) as error:
            annotations.dvid_capability(
                annotations.DVIDTarget("https://dvid.test", "abc123"),
                _user(),
                "view",
                _request(),
            )
    _assert_http_error(error, 403)
    assert error.value.detail == {
        "decision": "tos_required",
        "tos_url": "https://dsg.test/opaque",
    }


@pytest.mark.parametrize("broker_status", [401, 400, 403, 500, 503])
def test_broker_http_failures_are_closed(broker_status):
    with patch.object(
        annotations.requests, "post", return_value=_response(status_code=broker_status),
    ):
        with pytest.raises(HTTPException) as error:
            annotations.dvid_capability(
                annotations.DVIDTarget("https://dvid.test", "abc123"),
                _user(),
                "view",
                _request(),
            )
    expected = {401: 401, 400: 400}.get(broker_status, 502)
    _assert_http_error(error, expected)


def test_broker_network_invalid_json_and_missing_token_fail_closed():
    target = annotations.DVIDTarget("https://dvid.test", "abc123")
    with patch.object(
        annotations.requests, "post", side_effect=requests.ConnectionError("offline"),
    ):
        with pytest.raises(HTTPException) as error:
            annotations.dvid_capability(target, _user(), "view", _request())
    _assert_http_error(error, 502)

    invalid = _response()
    invalid.json.side_effect = ValueError("not json")
    with patch.object(annotations.requests, "post", return_value=invalid):
        with pytest.raises(HTTPException) as error:
            annotations.dvid_capability(target, _user(), "view", _request())
    _assert_http_error(error, 502)

    with pytest.raises(HTTPException) as error:
        annotations.dvid_capability(target, _user(token=None), "view", _request())
    _assert_http_error(error, 401)


def test_get_route_uses_one_view_broker_call_and_capability_only_on_data_call():
    target = annotations.DVIDTarget("https://dvid.test", "abc123")
    data_response = _response(content=b'{"1":{"bodyid":1}}')
    request = _request()
    user = _user()
    with patch.object(annotations, "resolve_dvid_target", return_value=target), patch.object(
        annotations, "dvid_capability", return_value="opaque",
    ) as broker, patch.object(annotations.requests, "get", return_value=data_response) as get:
        response = annotations.get_annotations(
            "ds", "1", request, version="abc123", user=user,
        )

    assert response == [{"bodyid": 1}]
    broker.assert_called_once_with(target, user, "view", request)
    get.assert_called_once_with(
        "https://dvid.test/api/node/abc123/segmentation_annotations/keyvalues?json=true",
        data="[1]",
        headers={"Authorization": "DVID-Capability opaque"},
    )
    assert "X-DVID-Internal" not in get.call_args.kwargs["headers"]
    assert "dsg-token" not in get.call_args.kwargs["headers"]["Authorization"]


def test_query_uses_view_capability_on_read_only_post():
    target = annotations.DVIDTarget("https://dvid.test", "abc123")
    with patch.object(annotations, "resolve_dvid_target", return_value=target), patch.object(
        annotations, "dvid_capability", return_value="opaque",
    ) as broker, patch.object(
        annotations.requests, "post", return_value=_response(content=b"[]"),
    ) as post:
        response = annotations.query_annotations(
            "ds", {"status": "done"}, _request(), user=_user(),
        )

    assert response.body == b"[]"
    assert broker.call_args.args[2] == "view"
    post.assert_called_once_with(
        "https://dvid.test/api/node/abc123/segmentation_annotations/query",
        json={"status": "done"},
        headers={"Authorization": "DVID-Capability opaque"},
    )


def test_multi_annotation_write_brokers_once_and_reuses_edit_capability():
    target = annotations.DVIDTarget("https://dvid.test", "abc123")
    payload = [{"bodyid": 1}, {"bodyid": 2}]
    user = _user(datasets={})
    request = _request()
    with patch.object(annotations, "resolve_dvid_target", return_value=target), patch.object(
        annotations, "dvid_capability", return_value="opaque",
    ) as broker, patch.object(
        annotations.requests, "post", return_value=_response(),
    ) as post:
        annotations.post_annotations("ds", payload, request, user=user)

    broker.assert_called_once_with(target, user, "edit", request)
    assert post.call_args_list == [
        call(
            "https://dvid.test/api/node/abc123/segmentation_annotations/key/1?u=user@test.com",
            json={"bodyid": 1},
            headers={"Authorization": "DVID-Capability opaque"},
        ),
        call(
            "https://dvid.test/api/node/abc123/segmentation_annotations/key/2?u=user@test.com",
            json={"bodyid": 2},
            headers={"Authorization": "DVID-Capability opaque"},
        ),
    ]


def test_delete_is_bound_to_head_and_uses_edit_capability():
    target = annotations.DVIDTarget("https://dvid.test", "head123")
    with patch.object(annotations, "resolve_dvid_target", return_value=target) as resolve, patch.object(
        annotations, "dvid_capability", return_value="opaque",
    ) as broker, patch.object(
        annotations.requests, "delete", return_value=_response(),
    ) as delete:
        annotations.delete_annotations("ds", "42", _request(), user=_user())

    resolve.assert_called_once_with("ds")
    assert broker.call_args.args[2] == "edit"
    delete.assert_called_once_with(
        "https://dvid.test/api/node/head123/segmentation_annotations/key/42",
        headers={"Authorization": "DVID-Capability opaque"},
    )


def test_streaming_get_carries_capability_header():
    with patch.object(
        annotations.requests, "get", return_value=_response(content=b"[]"),
    ) as get:
        chunks = asyncio.run(_collect(annotations.dvid_streaming_request(
            "https://dvid.test/data", "opaque",
        )))
    assert chunks == [b"[]"]
    get.assert_called_once_with(
        "https://dvid.test/data",
        headers={"Authorization": "DVID-Capability opaque"},
    )


async def _collect(iterator):
    return [chunk async for chunk in iterator]


@pytest.mark.parametrize(
    ("route", "args"),
    [
        (annotations.get_versions, ("ds",)),
        (annotations.get_head_tag, ("ds",)),
        (annotations.get_head_uuid, ("ds",)),
        (annotations.get_tag_to_uuid, ("ds", "v1")),
        (annotations.get_uuid_to_tag, ("ds", "abc")),
    ],
)
def test_metadata_only_routes_enforce_real_dataset_read_gate(route, args):
    user = _user(datasets={})
    with patch.object(annotations.cache, "get_value") as get_value:
        with pytest.raises(HTTPException) as error:
            route(*args, user=user)
    _assert_http_error(error, 403)
    get_value.assert_not_called()


def test_registered_metadata_endpoints_are_the_gated_functions_not_wrappers():
    endpoints = {route.endpoint for route in annotations.router.routes}
    assert annotations.get_versions in endpoints
    assert annotations.get_head_tag in endpoints
    assert annotations.get_head_uuid in endpoints
    assert annotations.get_tag_to_uuid in endpoints
    assert annotations.get_uuid_to_tag in endpoints


def test_annotation_route_signatures_generate_openapi(app):
    paths = app.openapi()["paths"]
    assert "/v2/json-annotations/{dataset}/neurons/fields" in paths
    assert "/v2/json-annotations/{dataset}/neurons/query" in paths
    assert "/v2/json-annotations/{dataset}/neurons" in paths


def test_dvid_route_does_not_preempt_version_grant_with_dataset_cache_gate():
    target = annotations.DVIDTarget("https://dvid.test", "version123")
    version_only_user = _user(datasets={})
    with patch.object(annotations, "resolve_dvid_target", return_value=target), patch.object(
        annotations, "dvid_capability", return_value="opaque",
    ) as broker, patch.object(
        annotations.requests, "get", return_value=_response(content=b"[]"),
    ):
        response = annotations.get_fields("ds", _request(), user=version_only_user)

    assert response.body == b"[]"
    broker.assert_called_once()
    assert broker.call_args.args[1] is version_only_user
    assert broker.call_args.args[2] == "view"
