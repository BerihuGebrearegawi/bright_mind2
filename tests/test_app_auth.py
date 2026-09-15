"""Real behavioural tests for app.py's auth routes. These are the routes
that create/authenticate accounts outside the normal Firebase
email+password flow (phone+PIN) and the admin-claims upgrade path, plus
the password-reset proxy to Firebase's Identity Toolkit REST API.

phone-pin register/login/admin-token call `firebase_admin.auth` directly
(get_user_by_phone_number/create_user/create_custom_token) rather than
only going through _require_user_bearer, so these tests use the
`fake_firebase_auth` fixture in addition to the usual `fake_db`.
"""
import pytest


def require_user_as(app_module, monkeypatch, uid="user-1", **extra):
    detail = {"uid": uid, "email": f"{uid}@example.com", **extra}
    monkeypatch.setattr(app_module, "_require_user_bearer", lambda: (True, detail))
    return detail


def require_user_denied(app_module, monkeypatch, status=401):
    monkeypatch.setattr(app_module, "_require_user_bearer",
                         lambda: (False, ({"error": "Sign in required."}, status)))


def _client(app_module):
    return app_module.app.test_client()


def _configure_firebase(app_module, monkeypatch, configured=True):
    monkeypatch.setattr(app_module, "_firebase_admin_from_env", lambda: (True if configured else None))


VALID_PHONE = "0912345678"
NORMALIZED_PHONE = "+251912345678"


class TestForgotPassword:
    def test_invalid_email_is_rejected(self, app_module):
        r = _client(app_module).post("/api/auth/forgot-password", json={"email": "not-an-email"})
        assert r.status_code == 400

    def test_not_configured_returns_503(self, app_module, monkeypatch):
        monkeypatch.delenv("FIREBASE_WEB_API_KEY", raising=False)
        r = _client(app_module).post("/api/auth/forgot-password", json={"email": "a@b.com"})
        assert r.status_code == 503

    def test_success_returns_generic_message(self, app_module, monkeypatch):
        monkeypatch.setenv("FIREBASE_WEB_API_KEY", "web-key")
        import requests as requests_module
        from tests.fakes import FakeHttpResponse
        monkeypatch.setattr(requests_module, "post",
                             lambda url, json=None, timeout=None: FakeHttpResponse(200, {}))
        r = _client(app_module).post("/api/auth/forgot-password", json={"email": "real@example.com"})
        assert r.status_code == 200
        assert r.get_json()["success"] is True

    def test_unknown_email_still_returns_generic_success(self, app_module, monkeypatch):
        """Account enumeration must be impossible: EMAIL_NOT_FOUND from
        Firebase is normalized into the same success response as a real
        account, not surfaced as an error."""
        monkeypatch.setenv("FIREBASE_WEB_API_KEY", "web-key")
        import requests as requests_module
        from tests.fakes import FakeHttpResponse
        monkeypatch.setattr(requests_module, "post", lambda url, json=None, timeout=None:
                             FakeHttpResponse(400, {"error": {"message": "EMAIL_NOT_FOUND"}}))
        r = _client(app_module).post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
        assert r.status_code == 200
        assert r.get_json()["success"] is True

    def test_bad_server_configuration_is_normalized_to_503(self, app_module, monkeypatch):
        monkeypatch.setenv("FIREBASE_WEB_API_KEY", "wrong-key")
        import requests as requests_module
        from tests.fakes import FakeHttpResponse
        monkeypatch.setattr(requests_module, "post", lambda url, json=None, timeout=None:
                             FakeHttpResponse(400, {"error": {"message": "API_KEY_INVALID"}}))
        r = _client(app_module).post("/api/auth/forgot-password", json={"email": "a@b.com"})
        assert r.status_code == 503


class TestPhonePinRegister:
    def test_invalid_role_is_rejected(self, fake_db, app_module, monkeypatch):
        r = _client(app_module).post("/api/auth/phone-pin/register",
                                      json={"role": "admin", "name": "X", "phone": VALID_PHONE, "pin": "123456"})
        assert r.status_code == 400

    def test_missing_name_is_rejected(self, fake_db, app_module):
        r = _client(app_module).post("/api/auth/phone-pin/register",
                                      json={"role": "student", "phone": VALID_PHONE, "pin": "123456", "className": "7"})
        assert r.status_code == 400

    def test_invalid_phone_is_rejected(self, fake_db, app_module):
        r = _client(app_module).post("/api/auth/phone-pin/register",
                                      json={"role": "student", "name": "X", "phone": "12345", "pin": "123456", "className": "7"})
        assert r.status_code == 400

    def test_invalid_pin_is_rejected(self, fake_db, app_module):
        r = _client(app_module).post("/api/auth/phone-pin/register",
                                      json={"role": "student", "name": "X", "phone": VALID_PHONE, "pin": "12", "className": "7"})
        assert r.status_code == 400

    def test_student_without_class_is_rejected(self, fake_db, app_module):
        r = _client(app_module).post("/api/auth/phone-pin/register",
                                      json={"role": "student", "name": "X", "phone": VALID_PHONE, "pin": "123456"})
        assert r.status_code == 400

    def test_firebase_not_configured_returns_503(self, fake_db, app_module, monkeypatch):
        _configure_firebase(app_module, monkeypatch, configured=False)
        r = _client(app_module).post("/api/auth/phone-pin/register",
                                      json={"role": "student", "name": "X", "phone": VALID_PHONE, "pin": "123456", "className": "7"})
        assert r.status_code == 503

    def test_already_registered_phone_is_rejected(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.seed_user("existing-uid", phone_number=NORMALIZED_PHONE)
        r = _client(app_module).post("/api/auth/phone-pin/register",
                                      json={"role": "student", "name": "X", "phone": VALID_PHONE, "pin": "123456", "className": "7"})
        assert r.status_code == 409

    def test_successful_student_registration(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        r = _client(app_module).post("/api/auth/phone-pin/register",
                                      json={"role": "student", "name": "Abebe", "phone": VALID_PHONE,
                                            "pin": "123456", "className": "7", "region": "Addis Ababa"})
        assert r.status_code == 201
        body = r.get_json()
        uid = body["uid"]
        user_doc = fake_db.dump("users")[uid]
        assert user_doc["phone"] == NORMALIZED_PHONE
        assert user_doc["role"] == "student"
        assert user_doc["isAdmin"] is False
        # Auth secret stores a PIN *hash*, never the raw PIN.
        secret = fake_db.dump("authSecrets")[uid]
        assert "123456" not in secret["pinHash"]
        assert secret["pinHash"].startswith("scrypt$")
        assert len(fake_db.dump("teacherRequests")) == 0

    def test_teacher_registration_creates_pending_teacher_request(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        r = _client(app_module).post("/api/auth/phone-pin/register",
                                      json={"role": "teacher", "name": "Teacher T", "phone": VALID_PHONE, "pin": "123456"})
        assert r.status_code == 201
        requests_ = list(fake_db.dump("teacherRequests").values())
        assert len(requests_) == 1
        assert requests_[0]["status"] == "pending"


class TestPhonePinLogin:
    def test_invalid_input_is_rejected(self, fake_db, app_module):
        r = _client(app_module).post("/api/auth/phone-pin/login", json={"phone": "bad", "pin": "1"})
        assert r.status_code == 400

    def test_unknown_phone_is_rejected(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        r = _client(app_module).post("/api/auth/phone-pin/login", json={"phone": VALID_PHONE, "pin": "123456"})
        assert r.status_code == 401

    def test_admin_account_cannot_sign_in_with_pin(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.seed_user("admin-uid", phone_number=NORMALIZED_PHONE)
        fake_db.seed("users", "admin-uid", {"isAdmin": True, "role": "admin"})
        r = _client(app_module).post("/api/auth/phone-pin/login", json={"phone": VALID_PHONE, "pin": "123456"})
        assert r.status_code == 403

    def test_role_mismatch_is_rejected(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.seed_user("t-uid", phone_number=NORMALIZED_PHONE)
        fake_db.seed("users", "t-uid", {"role": "teacher"})
        fake_db.seed("authSecrets", "t-uid", {"pinHash": app_module._hash_pin("123456")})
        r = _client(app_module).post("/api/auth/phone-pin/login",
                                      json={"phone": VALID_PHONE, "pin": "123456", "role": "student"})
        assert r.status_code == 403

    def test_wrong_pin_is_rejected(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.seed_user("s-uid", phone_number=NORMALIZED_PHONE)
        fake_db.seed("users", "s-uid", {"role": "student"})
        fake_db.seed("authSecrets", "s-uid", {"pinHash": app_module._hash_pin("999999")})
        r = _client(app_module).post("/api/auth/phone-pin/login", json={"phone": VALID_PHONE, "pin": "123456"})
        assert r.status_code == 401

    def test_successful_login_returns_custom_token(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.seed_user("s-uid", phone_number=NORMALIZED_PHONE)
        fake_db.seed("users", "s-uid", {"role": "student", "phoneVerified": True})
        fake_db.seed("authSecrets", "s-uid", {"pinHash": app_module._hash_pin("123456")})
        r = _client(app_module).post("/api/auth/phone-pin/login", json={"phone": VALID_PHONE, "pin": "123456"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["uid"] == "s-uid"
        assert body["role"] == "student"
        assert body["token"] == "fake-custom-token:s-uid"
        assert fake_firebase_auth.custom_tokens[-1] == ("s-uid", {"role": "student", "phoneLogin": True})


class TestAdminTokenUpgrade:
    def test_signed_out_is_rejected(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        require_user_denied(app_module, monkeypatch)
        r = _client(app_module).post("/api/auth/admin-token")
        assert r.status_code == 401

    def test_non_admin_profile_is_rejected(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake_db.seed("users", "user-1", {"role": "student"})
        r = _client(app_module).post("/api/auth/admin-token")
        assert r.status_code == 403

    def test_admin_flagged_profile_gets_custom_token(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake_db.seed("users", "user-1", {"isAdmin": True})
        r = _client(app_module).post("/api/auth/admin-token")
        assert r.status_code == 200
        body = r.get_json()
        assert body["role"] == "admin"
        assert fake_firebase_auth.custom_tokens[-1] == ("user-1", {"admin": True, "role": "admin"})


class TestPhonePinMarkVerified:
    def test_signed_out_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_denied(app_module, monkeypatch)
        r = _client(app_module).post("/api/auth/phone-pin/mark-verified")
        assert r.status_code == 401

    def test_missing_phone_claim_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        r = _client(app_module).post("/api/auth/phone-pin/mark-verified")
        assert r.status_code == 400

    def test_success_updates_user_doc(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1", phone_number=NORMALIZED_PHONE)
        r = _client(app_module).post("/api/auth/phone-pin/mark-verified")
        assert r.status_code == 200
        user = fake_db.dump("users")["user-1"]
        assert user["phoneVerified"] is True
        assert user["phone"] == NORMALIZED_PHONE
