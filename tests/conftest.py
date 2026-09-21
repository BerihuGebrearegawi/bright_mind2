import sys
import types
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fakes import (  # noqa: E402
    FakeFirestore,
    FakeTelegramAPI,
    fake_transactional,
    FakeIncrement,
    FakeCloudinaryAPI,
    FakeGeminiAPI,
    FakeChapaAPI,
    FakeTelegramBotAdminAPI,
    FakeFirebaseAuth,
    FakeMessaging,
    SERVER_TIMESTAMP,
)

# --- Stub out firebase_admin so route modules can `from firebase_admin import
# firestore` without the real package being installed. Only installed if not
# already present, so this never shadows a real firebase_admin in CI/prod. ---
_current_db = {"db": None}


def _fake_client():
    if _current_db["db"] is None:
        raise AssertionError(
            "A test called firestore.client() without seeding a FakeFirestore db first "
            "(use the `fake_db` fixture)."
        )
    return _current_db["db"]


if "firebase_admin" not in sys.modules:
    fake_firestore_module = types.ModuleType("firebase_admin.firestore")
    fake_firestore_module.client = _fake_client
    fake_firestore_module.transactional = fake_transactional
    fake_firestore_module.Increment = FakeIncrement
    fake_firestore_module.SERVER_TIMESTAMP = SERVER_TIMESTAMP

    class _FakeQueryDirection:
        DESCENDING = "DESCENDING"
        ASCENDING = "ASCENDING"

    fake_firestore_module.Query = _FakeQueryDirection
    fake_firebase_admin_module = types.ModuleType("firebase_admin")
    fake_firebase_admin_module.firestore = fake_firestore_module
    sys.modules["firebase_admin"] = fake_firebase_admin_module
    sys.modules["firebase_admin.firestore"] = fake_firestore_module


@pytest.fixture
def fake_db():
    """A fresh in-memory Firestore for each test, wired up so that any code
    path calling `firestore.client()` gets this instance."""
    db = FakeFirestore()
    _current_db["db"] = db
    yield db
    _current_db["db"] = None


@pytest.fixture
def firebase_configured():
    """A firebase_admin_factory that reports 'configured'."""
    return lambda: True


@pytest.fixture
def firebase_not_configured():
    """A firebase_admin_factory that reports 'not configured' (no credentials)."""
    return lambda: None


@pytest.fixture
def telegram_api(monkeypatch):
    """Patches requests.post globally (some route modules import `requests`
    at module level, one imports it locally inside a function - patching the
    single shared module in sys.modules covers both) so no real network call
    is ever made, and returns the fake for call assertions."""
    import requests as requests_module
    fake = FakeTelegramAPI()
    monkeypatch.setattr(requests_module, "post", fake.post)
    return fake


@pytest.fixture
def cloudinary_api(monkeypatch):
    """Patches requests.post globally so calls to api.cloudinary.com never
    hit the network; returns the fake for call assertions. Mirrors
    telegram_api below but with a Cloudinary-shaped response."""
    import requests as requests_module
    fake = FakeCloudinaryAPI()
    monkeypatch.setattr(requests_module, "post", fake.post)
    return fake


@pytest.fixture
def gemini_api(monkeypatch):
    """Patches requests.post globally so calls to
    generativelanguage.googleapis.com never hit the network. Returns the
    fake with a default single-question response; tests that need specific
    extracted content/failure modes construct their own FakeGeminiAPI and
    patch it directly instead of using this fixture."""
    import requests as requests_module
    fake = FakeGeminiAPI()
    monkeypatch.setattr(requests_module, "post", fake.post)
    return fake


@pytest.fixture
def chapa_api(monkeypatch):
    """Patches requests.request globally (app.py's Chapa helper calls
    requests.request, not requests.post) so calls to api.chapa.co never hit
    the network. Returns the fake with a default successful
    initialize+verify response."""
    import requests as requests_module
    fake = FakeChapaAPI()
    monkeypatch.setattr(requests_module, "request", fake.request)
    return fake


@pytest.fixture
def app_module():
    """Imports app.py (the monolithic Flask app) once per test and returns
    the module, so tests can monkeypatch its module-level
    _require_user_bearer/_require_admin_bearer directly and get a client
    via app_module.app.test_client(). Importing app.py registers every
    sub-module's routes too (they capture their own auth functions as
    arguments at registration time, independent of anything patched here
    afterwards) - this fixture only ever affects app.py's own inline
    routes."""
    import app as app_module
    return app_module


@pytest.fixture
def fake_messaging(monkeypatch):
    """Installs a FakeMessaging as firebase_admin.messaging for routes that
    send FCM push (admin announcements' optional push, /api/notifications/
    send). Returns the fake so tests can script per-token outcomes via
    `fake.outcomes = [...]` before calling the route."""
    import types
    import firebase_admin
    fake = FakeMessaging()
    mod = types.ModuleType("firebase_admin.messaging")
    mod.Message = fake.Message
    mod.Notification = fake.Notification
    mod.send_each = fake.send_each
    monkeypatch.setitem(sys.modules, "firebase_admin.messaging", mod)
    monkeypatch.setattr(firebase_admin, "messaging", mod, raising=False)
    return fake


@pytest.fixture
def fake_firebase_auth(monkeypatch):
    """Installs a FakeFirebaseAuth as firebase_admin.auth for routes that
    call it directly (phone+PIN register/login, admin-token) rather than
    only going through _require_user_bearer/_require_admin_bearer (which
    tests normally bypass entirely via monkeypatch). Auto-reverts via
    monkeypatch's own teardown, so it never leaks into other tests."""
    import types
    import firebase_admin
    fake = FakeFirebaseAuth()
    mod = types.ModuleType("firebase_admin.auth")
    mod.get_user_by_phone_number = fake.get_user_by_phone_number
    mod.create_user = fake.create_user
    mod.create_custom_token = fake.create_custom_token
    mod.verify_id_token = fake.verify_id_token
    mod.revoke_refresh_tokens = fake.revoke_refresh_tokens
    mod.UserNotFoundError = fake.UserNotFoundError
    monkeypatch.setitem(sys.modules, "firebase_admin.auth", mod)
    monkeypatch.setattr(firebase_admin, "auth", mod, raising=False)
    return fake


@pytest.fixture(autouse=True)
def _reset_app_rate_limits():
    """app.py's rate limiter uses a module-level, never-reset-between-tests
    dict keyed by client IP + path. The Flask test client always presents
    the same IP, so without a reset the limiter's own 30-requests-per-60s
    default would start rejecting later tests in any file that exercises
    the same endpoint repeatedly (auth guard, validation, happy path, ...).
    Only touches state if tests/test_app_*.py has actually imported the
    `app` module - other test files never trigger this import."""
    app_module = sys.modules.get("app")
    if app_module is not None:
        app_module._rate_buckets.clear()
    yield


def make_app(register_fn, *args, **kwargs):
    app = Flask(__name__)
    app.testing = True
    register_fn(app, *args, **kwargs)
    return app
