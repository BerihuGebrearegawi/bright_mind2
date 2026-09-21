"""Tests for the admin-toggleable account suspension feature.

Unlike most other app.py tests, the tests in TestSuspensionEnforcement
below deliberately do NOT monkeypatch _require_user_bearer - that's the
one function the enforcement lives in (_check_not_suspended, called from
inside the real _require_user_bearer), so it has to run for real. This
means these tests script a bearer token via fake_firebase_auth.
script_id_token() and drive an arbitrary already-tested route
(/api/student/profile-details) purely as a vehicle to exercise the real
auth gate - the route itself isn't what's under test here.

The admin-facing toggle endpoint (POST /api/admin/users/<uid>/suspend)
and the phone+PIN login block use the same monkeypatch-require_admin /
scripted-phone patterns as the rest of the app.py test suite.
"""
from datetime import datetime, timezone

import pytest


def _configure_firebase(app_module, monkeypatch, configured=True):
    monkeypatch.setattr(app_module, "_firebase_admin_from_env", lambda: (True if configured else None))


def _client(app_module):
    return app_module.app.test_client()


def _require_user_bearer_result(app_module, token=None):
    """Calls the real _require_user_bearer() directly inside a request
    context, instead of going through some arbitrary downstream route.

    This sidesteps an unrelated wrinkle: routes registered via
    register_student_profile_routes(...) (and every other register_x_routes
    call in app.py) were handed _firebase_admin_from_env as a plain
    function reference at import time, so monkeypatching
    app_module._firebase_admin_from_env later doesn't reach those modules'
    own closures - only app.py's own inline routes see it. Calling
    _require_user_bearer() straight from app.py avoids that mismatch
    entirely and tests exactly the function _check_not_suspended lives in.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with app_module.app.test_request_context(headers=headers):
        return app_module._require_user_bearer()


class TestSuspensionEnforcement:
    """Exercises the real _require_user_bearer -> _check_not_suspended
    path, not a monkeypatched stand-in."""

    def test_non_suspended_user_passes_through(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.script_id_token("tok-1", "user-1")
        fake_db.seed("users", "user-1", {"role": "student"})
        ok, detail = _require_user_bearer_result(app_module, "tok-1")
        assert ok is True
        assert detail["uid"] == "user-1"

    def test_suspended_user_is_blocked_with_403(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.script_id_token("tok-2", "user-2")
        fake_db.seed("users", "user-2", {"role": "student", "isSuspended": True})
        ok, (body, status) = _require_user_bearer_result(app_module, "tok-2")
        assert ok is False
        assert status == 403
        assert "suspended" in body.get_json()["error"].lower()

    def test_suspension_reason_is_surfaced_in_the_error(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.script_id_token("tok-3", "user-3")
        fake_db.seed("users", "user-3", {"role": "student", "isSuspended": True,
                                          "suspendReason": "Repeated cheating on exams"})
        ok, (body, status) = _require_user_bearer_result(app_module, "tok-3")
        assert status == 403
        assert "Repeated cheating on exams" in body.get_json()["error"]

    def test_unsuspended_user_is_no_longer_blocked(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.script_id_token("tok-4", "user-4")
        fake_db.seed("users", "user-4", {"role": "student", "isSuspended": False})
        ok, detail = _require_user_bearer_result(app_module, "tok-4")
        assert ok is True

    def test_missing_user_doc_is_not_treated_as_suspended(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.script_id_token("tok-5", "user-with-no-profile-doc")
        ok, detail = _require_user_bearer_result(app_module, "tok-5")
        assert ok is True

    def test_revoked_token_is_still_a_plain_401_not_a_suspension_message(
        self, fake_db, app_module, monkeypatch, fake_firebase_auth
    ):
        # revoke_refresh_tokens() is called as a defense-in-depth extra on
        # top of the isSuspended flag (see admin_suspend_user) - this just
        # confirms the two failure modes stay distinguishable: a revoked
        # token fails at verify_id_token() itself (401, generic), never
        # reaching (or needing) the Firestore suspension check (403).
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.script_id_token("tok-6", "user-6")
        fake_firebase_auth.revoke_refresh_tokens("user-6")
        ok, (body, status) = _require_user_bearer_result(app_module, "tok-6")
        assert status == 401

    def test_suspension_check_fails_closed_on_firestore_error(
        self, fake_db, app_module, monkeypatch, fake_firebase_auth
    ):
        # Security requirement: if the suspension status cannot be verified,
        # the request must fail closed rather than bypassing the suspension gate.
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.script_id_token("tok-7", "user-7")

        def broken_collection(name):
            raise RuntimeError("Firestore is down")

        monkeypatch.setattr(fake_db, "collection", broken_collection)
        ok, (body, status) = _require_user_bearer_result(app_module, "tok-7")
        assert ok is False
        assert status == 503

    def test_preview_session_is_server_side_read_only(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.script_id_token("tok-preview", "user-preview", previewSession=True, previewRole="student")
        fake_db.seed("users", "user-preview", {"role": "student"})
        with app_module.app.test_request_context(method="POST", headers={"Authorization": "Bearer tok-preview"}):
            ok, (body, status) = app_module._require_user_bearer()
        assert ok is False
        assert status == 403
        assert "read-only" in body.get_json()["error"].lower()

    def test_no_token_is_still_a_plain_401(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        ok, (body, status) = _require_user_bearer_result(app_module, None)
        assert status == 401


class TestAdminSuspendEndpoint:
    """These use make_app() (see tests/conftest.py) to register only
    student_profile_routes onto a bare Flask app with test-controlled
    require_user/require_admin/firebase_admin_factory closures - the same
    pattern test_telegram_platform_routes.py uses - rather than the shared
    app_module fixture. That's necessary here, not just a style choice:
    app.py's app_module fixture only lets you monkeypatch
    app_module._require_admin_bearer for app.py's OWN inline routes.
    Every register_x_routes(...) call in app.py (including
    register_student_profile_routes, where this endpoint actually lives)
    was handed that function as a plain reference at import time, so a
    later monkeypatch on the module attribute never reaches it.
    """

    def _client(self, require_admin, firebase_configured=True):
        from student_profile_routes import register_student_profile_routes
        from tests.conftest import make_app
        require_user = lambda: (True, {"uid": "irrelevant-here"})
        factory = (lambda: True) if firebase_configured else (lambda: None)
        app = make_app(register_student_profile_routes, require_user, require_admin, factory)
        return app.test_client()

    def _admin_ok(self, uid="admin-1"):
        return lambda: (True, {"uid": uid, "admin": True})

    def _admin_denied(self):
        return lambda: (False, ({"error": "Admin access required."}, 403))

    def test_non_admin_is_rejected(self, fake_db):
        r = self._client(self._admin_denied()).post("/api/admin/users/user-1/suspend", json={"suspended": True})
        assert r.status_code == 403

    def test_admin_cannot_suspend_self(self, fake_db):
        r = self._client(self._admin_ok("admin-1")).post("/api/admin/users/admin-1/suspend", json={"suspended": True})
        assert r.status_code == 400

    def test_unknown_user_is_rejected(self, fake_db):
        r = self._client(self._admin_ok()).post("/api/admin/users/nobody/suspend", json={"suspended": True})
        assert r.status_code == 404

    def test_admin_target_cannot_be_suspended(self, fake_db):
        fake_db.seed("users", "other-admin", {"isAdmin": True})
        r = self._client(self._admin_ok()).post("/api/admin/users/other-admin/suspend", json={"suspended": True})
        assert r.status_code == 400

    def test_suspending_sets_flag_reason_and_revokes_tokens(self, fake_db, monkeypatch, fake_firebase_auth):
        fake_db.seed("users", "user-1", {"role": "student"})
        r = self._client(self._admin_ok("admin-1")).post(
            "/api/admin/users/user-1/suspend", json={"suspended": True, "reason": "Payment fraud"})
        assert r.status_code == 200
        assert r.get_json()["isSuspended"] is True
        user = fake_db.dump("users")["user-1"]
        assert user["isSuspended"] is True
        assert user["suspendReason"] == "Payment fraud"
        assert user["suspendedBy"] == "admin-1"
        assert isinstance(user["suspendedAt"], datetime)
        assert "user-1" in fake_firebase_auth.revoked_uids

    def test_unsuspending_clears_the_flag_and_reason(self, fake_db):
        fake_db.seed("users", "user-1", {
            "role": "student", "isSuspended": True, "suspendReason": "old reason",
            "suspendedAt": datetime.now(timezone.utc), "suspendedBy": "admin-1",
        })
        r = self._client(self._admin_ok()).post("/api/admin/users/user-1/suspend", json={"suspended": False})
        assert r.status_code == 200
        user = fake_db.dump("users")["user-1"]
        assert user["isSuspended"] is False
        assert user["suspendReason"] == ""
        assert user["suspendedAt"] is None
        assert user["suspendedBy"] is None

    def test_default_action_is_suspend_when_field_omitted(self, fake_db):
        # suspended defaults to True so the endpoint is still safe to call
        # with just {} from a hurried admin - it never silently unsuspends.
        fake_db.seed("users", "user-1", {"role": "student"})
        r = self._client(self._admin_ok()).post("/api/admin/users/user-1/suspend", json={})
        assert r.status_code == 200
        assert fake_db.dump("users")["user-1"]["isSuspended"] is True


class TestPhonePinLoginBlocksSuspendedAccounts:
    VALID_PHONE = "0912345678"
    NORMALIZED_PHONE = "+251912345678"

    def test_suspended_account_cannot_log_in(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.seed_user("s-uid", phone_number=self.NORMALIZED_PHONE)
        fake_db.seed("users", "s-uid", {"role": "student", "isSuspended": True,
                                         "suspendReason": "Fee dispute"})
        fake_db.seed("authSecrets", "s-uid", {"pinHash": app_module._hash_pin("123456")})
        r = _client(app_module).post("/api/auth/phone-pin/login",
                                      json={"phone": self.VALID_PHONE, "pin": "123456"})
        assert r.status_code == 403
        assert "Fee dispute" in r.get_json()["error"]
        # No token should ever be minted for a suspended account.
        assert fake_firebase_auth.custom_tokens == []

    def test_unsuspended_account_can_still_log_in(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.seed_user("s-uid", phone_number=self.NORMALIZED_PHONE)
        fake_db.seed("users", "s-uid", {"role": "student", "isSuspended": False})
        fake_db.seed("authSecrets", "s-uid", {"pinHash": app_module._hash_pin("123456")})
        r = _client(app_module).post("/api/auth/phone-pin/login",
                                      json={"phone": self.VALID_PHONE, "pin": "123456"})
        assert r.status_code == 200
