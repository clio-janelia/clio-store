"""App-wide read-only contract for dedicated DatasetGateway service accounts."""

import re
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

import main  # noqa: F401 -- register every app route before enumerating them
from dependencies import (
    User,
    _enforce_service_account_mutation,
    app,
    enforce_service_account_read_only,
)
from services.datasets import get_datasets as get_datasets_route


WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _route_path(path: str) -> str:
    return re.sub(r"{[^}]+}", "sa-test", path)


MUTATION_ROUTES = sorted([
    (method, _route_path(route.path), route)
    for route in app.routes
    if isinstance(route, APIRoute)
    for method in route.methods & WRITE_METHODS
], key=lambda item: (item[0], item[1]))


def _service_account(**kwargs) -> User:
    values = {
        "email": "agent-reader@service-account.dsg.local",
        "name": "agent-reader",
        "service_account": True,
        "datasets": {"granted": {"clio_general"}},
        "datasets_ignore_tos": {"granted": {"clio_general"}},
        "token": "sa-token",
    }
    values.update(kwargs)
    return User(**values)


@pytest.mark.parametrize(("method", "path", "route"), MUTATION_ROUTES)
def test_service_account_is_forbidden_on_every_mutation_route(method, path, route):
    assert any(
        dependency.call is enforce_service_account_read_only
        for dependency in route.dependant.dependencies
    )
    request = Request({
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
    })
    with patch("dependencies._get_user_from_dsg", return_value=_service_account()):
        with pytest.raises(HTTPException) as error:
            _enforce_service_account_mutation(request, "sa-token")

    assert error.value.status_code == 403
    assert error.value.detail == "Service accounts are read-only in clio-store"


def test_view_granted_service_account_reads_only_resolved_datasets():
    granted = MagicMock()
    granted.id = "granted"
    granted.to_dict.return_value = {"title": "Granted"}
    denied = MagicMock()
    denied.id = "denied"
    denied.to_dict.return_value = {"title": "Denied"}
    collection = MagicMock()
    collection.stream.return_value = [granted, denied]

    with patch("services.datasets.firestore.get_collection", return_value=collection):
        response = get_datasets_route(
            templates=False,
            current_user=_service_account(),
        )

    assert response == {"granted": {"title": "Granted"}}
