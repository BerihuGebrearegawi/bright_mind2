"""Real behavioural tests for the remaining app.py inline routes not
covered by the payments/telegram-admin/auth/storage/exams test files:
admin preview tokens (the signed short-lived token that lets an admin
preview a dashboard as another role), the admin config diagnostic (with
its two independent auth paths - bearer admin OR a shared diagnostic
key), library/quiz/video listing (video listing has real entitlement
gating logic worth pinning down), the entitlement lookup, the legacy
one-question quiz submission, and a smoke test over the static/health
pages.
"""
from datetime import datetime, timedelta, timezone

import pytest


def require_user_as(app_module, monkeypatch, uid="user-1", **extra):
    detail = {"uid": uid, **extra}
    monkeypatch.setattr(app_module, "_require_user_bearer", lambda: (True, detail))
    return detail


def require_user_denied(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "_require_user_bearer",
                         lambda: (False, ({"error": "Sign in required."}, 401)))


def require_admin_as(app_module, monkeypatch, uid="admin-1"):
    monkeypatch.setattr(app_module, "_require_admin_bearer", lambda: (True, {"uid": uid, "admin": True}))


def require_admin_denied(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "_require_admin_bearer",
                         lambda: (False, ({"error": "Admin access required."}, 403)))


def _client(app_module):
    return app_module.app.test_client()


class TestStaticAndHealthPages:
    @pytest.mark.parametrize("path", ["/", "/admin", "/teacher", "/student", "/parent", "/auth"])
    def test_page_renders(self, app_module, path):
        r = _client(app_module).get(path)
        assert r.status_code == 200

    def test_healthz(self, app_module):
        r = _client(app_module).get("/healthz")
        assert r.status_code == 200
        assert r.get_json()["status"] == "ok"

    def test_readyz_reports_checks(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "_firebase_admin_from_env", lambda: None)
        r = _client(app_module).get("/readyz")
        assert r.status_code == 503
        assert r.get_json()["checks"]["firebase"] is False


class TestAdminPreviewToken:
    """Preview tokens are now bound to one specific real (role, uid) pair —
    /api/admin/preview-token mints a single-use ticket only after checking
    that account exists with a matching role, and /api/admin/preview-signin
    exchanges that ticket exactly once for a real Firebase sign-in token for
    that uid (see app.py's V31.107 rework: previews show genuine dashboard
    data instead of fabricated placeholder content)."""

    def test_non_admin_is_rejected(self, app_module, monkeypatch):
        require_admin_denied(app_module, monkeypatch)
        r = _client(app_module).post("/api/admin/preview-token", json={"role": "student", "uid": "s-1"})
        assert r.status_code == 403

    def test_invalid_role_is_rejected(self, app_module, monkeypatch, fake_db):
        require_admin_as(app_module, monkeypatch)
        monkeypatch.setenv("ADMIN_PREVIEW_SECRET", "s3cret")
        r = _client(app_module).post("/api/admin/preview-token", json={"role": "admin", "uid": "s-1"})
        assert r.status_code == 400

    def test_missing_uid_is_rejected(self, app_module, monkeypatch, fake_db):
        require_admin_as(app_module, monkeypatch)
        monkeypatch.setenv("ADMIN_PREVIEW_SECRET", "s3cret")
        r = _client(app_module).post("/api/admin/preview-token", json={"role": "student", "uid": ""})
        assert r.status_code == 400

    def test_unknown_uid_is_rejected(self, app_module, monkeypatch, fake_db):
        require_admin_as(app_module, monkeypatch)
        monkeypatch.setenv("ADMIN_PREVIEW_SECRET", "s3cret")
        r = _client(app_module).post("/api/admin/preview-token", json={"role": "student", "uid": "ghost"})
        assert r.status_code == 404

    def test_role_mismatch_is_rejected(self, app_module, monkeypatch, fake_db):
        require_admin_as(app_module, monkeypatch)
        monkeypatch.setenv("ADMIN_PREVIEW_SECRET", "s3cret")
        fake_db.seed("users", "teacher-9", {"role": "teacher", "name": "Kebede"})
        r = _client(app_module).post("/api/admin/preview-token", json={"role": "parent", "uid": "teacher-9"})
        assert r.status_code == 400

    def test_not_configured_returns_503(self, app_module, monkeypatch, fake_db):
        require_admin_as(app_module, monkeypatch)
        monkeypatch.delenv("ADMIN_PREVIEW_SECRET", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        fake_db.seed("users", "student-1", {"role": "student", "name": "Abebe"})
        r = _client(app_module).post("/api/admin/preview-token", json={"role": "student", "uid": "student-1"})
        assert r.status_code == 503

    def test_token_round_trips_through_signin(self, app_module, monkeypatch, fake_db, fake_firebase_auth):
        require_admin_as(app_module, monkeypatch, uid="admin-1")
        monkeypatch.setattr(app_module, "_firebase_admin_from_env", lambda: True)
        monkeypatch.setenv("ADMIN_PREVIEW_SECRET", "s3cret")
        fake_db.seed("users", "teacher-9", {"role": "teacher", "name": "Kebede"})
        client = _client(app_module)
        token = client.post("/api/admin/preview-token", json={"role": "teacher", "uid": "teacher-9"}).get_json()["token"]
        r = client.get(f"/api/admin/preview-signin?role=teacher&uid=teacher-9&token={token}")
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is True
        assert body["uid"] == "teacher-9"
        assert fake_firebase_auth.custom_tokens[-1][0] == "teacher-9"
        # The exchange is audited against the admin who requested it.
        audit_rows = list(fake_db.dump("adminAuditLogs").values())
        assert any(row.get("adminUid") == "admin-1" and row.get("target") == "teacher-9" for row in audit_rows)

    def test_signin_rejects_wrong_uid(self, app_module, monkeypatch, fake_db, fake_firebase_auth):
        require_admin_as(app_module, monkeypatch)
        monkeypatch.setattr(app_module, "_firebase_admin_from_env", lambda: True)
        monkeypatch.setenv("ADMIN_PREVIEW_SECRET", "s3cret")
        fake_db.seed("users", "teacher-9", {"role": "teacher"})
        fake_db.seed("users", "teacher-8", {"role": "teacher"})
        client = _client(app_module)
        token = client.post("/api/admin/preview-token", json={"role": "teacher", "uid": "teacher-9"}).get_json()["token"]
        r = client.get(f"/api/admin/preview-signin?role=teacher&uid=teacher-8&token={token}")
        assert r.status_code == 401

    def test_signin_rejects_wrong_role(self, app_module, monkeypatch, fake_db, fake_firebase_auth):
        require_admin_as(app_module, monkeypatch)
        monkeypatch.setattr(app_module, "_firebase_admin_from_env", lambda: True)
        monkeypatch.setenv("ADMIN_PREVIEW_SECRET", "s3cret")
        fake_db.seed("users", "teacher-9", {"role": "teacher"})
        client = _client(app_module)
        token = client.post("/api/admin/preview-token", json={"role": "teacher", "uid": "teacher-9"}).get_json()["token"]
        r = client.get(f"/api/admin/preview-signin?role=parent&uid=teacher-9&token={token}")
        assert r.status_code == 401

    def test_signin_rejects_tampered_token(self, app_module, monkeypatch, fake_db, fake_firebase_auth):
        require_admin_as(app_module, monkeypatch)
        monkeypatch.setattr(app_module, "_firebase_admin_from_env", lambda: True)
        monkeypatch.setenv("ADMIN_PREVIEW_SECRET", "s3cret")
        fake_db.seed("users", "teacher-9", {"role": "teacher"})
        client = _client(app_module)
        token = client.post("/api/admin/preview-token", json={"role": "teacher", "uid": "teacher-9"}).get_json()["token"]
        tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
        r = client.get(f"/api/admin/preview-signin?role=teacher&uid=teacher-9&token={tampered}")
        assert r.status_code == 401

    def test_signin_rejects_expired_token(self, app_module, monkeypatch, fake_firebase_auth):
        monkeypatch.setenv("ADMIN_PREVIEW_SECRET", "s3cret")
        # Mint with a negative TTL so it's already expired.
        token = app_module._make_preview_token("teacher", "teacher-9", "admin-1", ttl_seconds=-10)
        monkeypatch.setattr(app_module, "_firebase_admin_from_env", lambda: True)
        r = _client(app_module).get(f"/api/admin/preview-signin?role=teacher&uid=teacher-9&token={token}")
        assert r.status_code == 401

    def test_signin_ticket_is_single_use(self, app_module, monkeypatch, fake_db, fake_firebase_auth):
        require_admin_as(app_module, monkeypatch)
        monkeypatch.setattr(app_module, "_firebase_admin_from_env", lambda: True)
        monkeypatch.setenv("ADMIN_PREVIEW_SECRET", "s3cret")
        fake_db.seed("users", "teacher-9", {"role": "teacher"})
        client = _client(app_module)
        token = client.post("/api/admin/preview-token", json={"role": "teacher", "uid": "teacher-9"}).get_json()["token"]
        first = client.get(f"/api/admin/preview-signin?role=teacher&uid=teacher-9&token={token}")
        assert first.status_code == 200
        replay = client.get(f"/api/admin/preview-signin?role=teacher&uid=teacher-9&token={token}")
        assert replay.status_code == 401


class TestAdminConfigStatus:
    def test_non_admin_without_diagnostic_key_is_rejected(self, app_module, monkeypatch):
        monkeypatch.delenv("DIAGNOSTIC_KEY", raising=False)
        require_admin_denied(app_module, monkeypatch)
        r = _client(app_module).get("/api/admin/config-status")
        assert r.status_code == 403

    def test_admin_bearer_is_sufficient(self, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch)
        r = _client(app_module).get("/api/admin/config-status")
        assert r.status_code == 200
        assert "checks" in r.get_json()

    def test_diagnostic_key_bypasses_admin_bearer(self, app_module, monkeypatch):
        monkeypatch.setenv("DIAGNOSTIC_KEY", "diag-secret")
        require_admin_denied(app_module, monkeypatch)  # would 403 without the key path
        r = _client(app_module).get("/api/admin/config-status", headers={"X-Diagnostic-Key": "diag-secret"})
        assert r.status_code == 200

    def test_wrong_diagnostic_key_falls_back_to_admin_check(self, app_module, monkeypatch):
        monkeypatch.setenv("DIAGNOSTIC_KEY", "diag-secret")
        require_admin_denied(app_module, monkeypatch)
        r = _client(app_module).get("/api/admin/config-status", headers={"X-Diagnostic-Key": "wrong"})
        assert r.status_code == 403


class TestLibraryList:
    def test_signed_out_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_denied(app_module, monkeypatch)
        r = _client(app_module).get("/api/library")
        assert r.status_code == 401

    def test_filters_by_class(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        fake_db.seed("users", "user-1", {"class": "7", "role": "student"})
        fake_db.seed("books", "b1", {"title": "Grade 7 Math", "className": "7"})
        fake_db.seed("books", "b2", {"title": "Grade 8 Math", "className": "8"})
        r = _client(app_module).get("/api/library?className=7")
        assert r.status_code == 200
        items = r.get_json()["items"]
        assert len(items) == 1
        assert items[0]["title"] == "Grade 7 Math"

    def test_dedupes_across_collections(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        fake_db.seed("users", "user-1", {"class": "7", "role": "student"})
        fake_db.seed("books", "same-id", {"title": "X", "className": "7"})
        fake_db.seed("teacherGuides", "same-id", {"title": "Y", "className": "7"})
        r = _client(app_module).get("/api/library")
        # Different source collections with the same doc id are NOT the same
        # library item (dedup key includes the collection), so both appear.
        titles = {i["title"] for i in r.get_json()["items"]}
        assert titles == {"X", "Y"}


class TestQuizzesList:
    def test_missing_class_name_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).get("/api/quizzes")
        assert r.status_code == 400

    def test_never_exposes_correct_answer(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        fake_db.seed("quizzes", "q1", {"className": "7", "question": "2+2?",
                                        "options": {"A": "3", "B": "4"}, "correctAnswer": "B"})
        r = _client(app_module).get("/api/quizzes?className=7")
        assert r.status_code == 200
        assert "correctAnswer" not in str(r.get_json())


class TestVideosList:
    def test_missing_class_name_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).get("/api/videos")
        assert r.status_code == 400

    def test_free_video_is_accessible_to_everyone(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        fake_db.seed("videos", "v1", {"className": "7", "isPaid": False})
        r = _client(app_module).get("/api/videos?className=7")
        item = r.get_json()["videos"][0]
        assert item["accessible"] is True
        assert "url" in item

    def test_paid_video_is_hidden_without_entitlement(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        fake_db.seed("videos", "v1", {"className": "7", "isPaid": True, "url": "https://secret"})
        r = _client(app_module).get("/api/videos?className=7")
        item = r.get_json()["videos"][0]
        assert item["accessible"] is False
        assert "url" not in item

    def test_paid_video_is_accessible_with_active_premium(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        fake_db.seed("entitlements", "student-1", {"premium": True,
                                                     "expiresAt": datetime.now(timezone.utc) + timedelta(days=5)})
        fake_db.seed("videos", "v1", {"className": "7", "isPaid": True, "url": "https://secret"})
        r = _client(app_module).get("/api/videos?className=7")
        item = r.get_json()["videos"][0]
        assert item["accessible"] is True
        assert item["url"] == "https://secret"

    def test_expired_premium_does_not_unlock(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        fake_db.seed("entitlements", "student-1", {"premium": True,
                                                     "expiresAt": datetime.now(timezone.utc) - timedelta(days=1)})
        fake_db.seed("videos", "v1", {"className": "7", "isPaid": True})
        r = _client(app_module).get("/api/videos?className=7")
        assert r.get_json()["videos"][0]["accessible"] is False

    def test_active_free_trial_unlocks_trial_eligible_video(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        fake_db.seed("users", "student-1", {"freeTrial": {"isActive": True}})
        fake_db.seed("videos", "v1", {"className": "7", "isPaid": True, "isFreeTrialAccessible": True, "url": "https://secret"})
        r = _client(app_module).get("/api/videos?className=7")
        item = r.get_json()["videos"][0]
        assert item["accessible"] is True

    def test_admin_sees_all_paid_videos(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="admin-1", admin=True)
        fake_db.seed("videos", "v1", {"className": "7", "isPaid": True, "url": "https://secret"})
        r = _client(app_module).get("/api/videos?className=7")
        assert r.get_json()["videos"][0]["accessible"] is True


class TestEntitlement:
    def test_no_entitlement_doc_reports_not_premium(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        r = _client(app_module).get("/api/payments/entitlement")
        assert r.status_code == 200
        assert r.get_json()["premium"] is False

    def test_expired_entitlement_reports_not_premium(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake_db.seed("entitlements", "user-1", {"premium": True,
                                                 "expiresAt": datetime.now(timezone.utc) - timedelta(days=1)})
        r = _client(app_module).get("/api/payments/entitlement")
        body = r.get_json()
        assert body["premium"] is False
        assert body["expired"] is True

    def test_active_entitlement_reports_premium(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake_db.seed("entitlements", "user-1", {"premium": True, "plan": "yearly",
                                                 "expiresAt": datetime.now(timezone.utc) + timedelta(days=5)})
        r = _client(app_module).get("/api/payments/entitlement")
        body = r.get_json()
        assert body["premium"] is True
        assert body["plan"] == "yearly"


class TestLegacyQuizResults:
    def test_missing_fields_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/quiz-results", json={})
        assert r.status_code == 400

    def test_quiz_not_found(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/quiz-results", json={"quizId": "nope", "selectedAnswer": "A"})
        assert r.status_code == 404

    def test_quiz_without_answer_key_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        fake_db.seed("quizzes", "q1", {"correctAnswer": ""})
        r = _client(app_module).post("/api/quiz-results", json={"quizId": "q1", "selectedAnswer": "A"})
        assert r.status_code == 409

    def test_correct_answer_scores_full_marks(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        fake_db.seed("quizzes", "q1", {"correctAnswer": "B"})
        r = _client(app_module).post("/api/quiz-results", json={"quizId": "q1", "selectedAnswer": "b"})
        assert r.status_code == 201
        body = r.get_json()
        assert body["score"] == 1
        assert body["percentage"] == 100
        result = list(fake_db.dump("quizResults").values())[0]
        assert result["studentUid"] == "student-1"

    def test_wrong_answer_scores_zero(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        fake_db.seed("quizzes", "q1", {"correctAnswer": "B"})
        r = _client(app_module).post("/api/quiz-results", json={"quizId": "q1", "selectedAnswer": "A"})
        assert r.get_json()["score"] == 0
