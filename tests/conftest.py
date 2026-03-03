"""conftest.py — Pre-import mocking.

This file is loaded by pytest before any test modules.  Module-level code
sets environment variables and injects mock Google Cloud SDK modules into
``sys.modules`` so the application can be imported without GCP credentials.
"""

import os
import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# 1. Environment — config.py reads these at import time
# ---------------------------------------------------------------------------
os.environ["DSG_URL"] = "http://dsg.test"
os.environ["OWNER"] = "owner@test.com"

# ---------------------------------------------------------------------------
# 2. Google Cloud SDK mocks
# ---------------------------------------------------------------------------
# stores/firestore.py executes at import time:
#     from google.cloud import firestore
#     db = firestore.Client()
#
# dependencies.py then does:
#     datasets = DatasetCache(collection=firestore.get_collection(...))
#     datasets.refresh_cache()         # iterates collection.get()
#
# We need Client().collection(x).get() → [] so the iteration is a no-op.

_mock_fs_module = MagicMock(name="google.cloud.firestore")
_mock_client = MagicMock(name="firestore.Client()")
_mock_collection = MagicMock(name="firestore.collection")
_mock_collection.get.return_value = []
_mock_client.collection.return_value = _mock_collection
_mock_fs_module.Client.return_value = _mock_client

# Parent namespace mocks — guarantees `from google.X import Y` works even
# without the real SDKs installed.
_mock_google = MagicMock(name="google")
_mock_gc = MagicMock(name="google.cloud")
_mock_gc.firestore = _mock_fs_module
_mock_auth = MagicMock(name="google.auth")
_mock_oauth2 = MagicMock(name="google.oauth2")
_mock_google.cloud = _mock_gc
_mock_google.auth = _mock_auth
_mock_google.oauth2 = _mock_oauth2

_modules = {
    "google": _mock_google,
    "google.cloud": _mock_gc,
    "google.cloud.firestore": _mock_fs_module,
    "google.cloud.bigquery": _mock_gc.bigquery,
    "google.cloud.bigquery_storage": _mock_gc.bigquery_storage,
    "google.cloud.storage": _mock_gc.storage,
    "google.cloud.logging": _mock_gc.logging,
    "google.cloud.core": _mock_gc.core,
    "google.api_core": MagicMock(name="google.api_core"),
    "google.auth": _mock_auth,
    "google.auth.transport": _mock_auth.transport,
    "google.auth.transport.requests": _mock_auth.transport.requests,
    "google.auth.exceptions": _mock_auth.exceptions,
    "google.oauth2": _mock_oauth2,
    "google.oauth2.id_token": _mock_oauth2.id_token,
}
sys.modules.update(_modules)

# ---------------------------------------------------------------------------
# 3. Fixtures
# ---------------------------------------------------------------------------
import pytest  # noqa: E402


@pytest.fixture
def app():
    """The FastAPI app with all routes wired."""
    import main  # noqa: F401 — triggers route registration
    from dependencies import app as _app
    return _app


@pytest.fixture
def client(app):
    """Starlette TestClient wrapping the fully-wired app."""
    from starlette.testclient import TestClient
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_dsg_caches():
    """Reset DSG in-memory caches between tests."""
    from dependencies import _dsg_user_cache, _dsg_group_members_cache
    _dsg_user_cache.clear()
    _dsg_group_members_cache.clear()
    yield
    _dsg_user_cache.clear()
    _dsg_group_members_cache.clear()


@pytest.fixture(autouse=True)
def _reset_public_datasets():
    """Save and restore datasets.public so tests don't leak state."""
    from dependencies import datasets
    saved = datasets.public.copy()
    yield
    datasets.public = saved
