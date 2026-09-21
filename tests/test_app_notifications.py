"""Real behavioural tests for app.py's notification endpoints: the
student/teacher-facing list/read/read-all routes, the admin announcement
broadcaster, and the raw FCM send endpoint (including its invalid-token
cleanup, since that's the one part of this surface with real logic beyond
straightforward Firestore reads/writes).
"""
import hashlib

import pytest


def require_user_as(app_module, monkeypatch, uid="user-1", **extra):
    detail = {"uid": uid, **extra}
    monkeypatch.setattr(app_module, "_require_user_bearer", lambda: (True, detail))
    return detail


def require_admin_as(app_module, monkeypatch, uid="admin-1"):
    monkeypatch.setattr(app_module, "_require_admin_bearer", lambda: (True, {"uid": uid, "admin": True}))


def require_admin_denied(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "_require_admin_bearer",
                         lambda: (False, ({"error": "Admin access required."}, 403)))


def _client(app_module):
    return app_module.app.test_client()


class TestListNotifications:
    def test_lists_own_and_broadcast_notifications(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake_db.seed("notifications", "n1", {"targetUid": "user-1", "title": "Mine", "read": False})
        fake_db.seed("notifications", "n2", {"targetUid": "all", "title": "Broadcast", "read": True})
        fake_db.seed("notifications", "n3", {"targetUid": "someone-else", "title": "Not mine", "read": False})
        r = _client(app_module).get("/api/notifications")
        assert r.status_code == 200
        body = r.get_json()
        ids = {n["id"] for n in body["notifications"]}
        assert ids == {"n1", "n2"}
        assert body["unread"] == 1


class TestMarkNotificationRead:
    def test_not_found(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        r = _client(app_module).post("/api/notifications/nope/read")
        assert r.status_code == 404

    def test_not_authorized_for_someone_elses_notification(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-2")
        fake_db.seed("notifications", "n1", {"targetUid": "user-1", "read": False})
        r = _client(app_module).post("/api/notifications/n1/read")
        assert r.status_code == 403

    def test_can_mark_own_notification_read(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake_db.seed("notifications", "n1", {"targetUid": "user-1", "read": False})
        r = _client(app_module).post("/api/notifications/n1/read")
        assert r.status_code == 200
        assert fake_db.dump("notifications")["n1"]["read"] is True

    def test_can_mark_broadcast_notification_read(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake_db.seed("notifications", "n1", {"targetUid": "all", "read": False})
        r = _client(app_module).post("/api/notifications/n1/read")
        assert r.status_code == 200


class TestMarkAllRead:
    def test_marks_only_unread_own_and_broadcast_notifications(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake_db.seed("notifications", "n1", {"targetUid": "user-1", "read": False})
        fake_db.seed("notifications", "n2", {"targetUid": "all", "read": False})
        fake_db.seed("notifications", "n3", {"targetUid": "user-1", "read": True})
        fake_db.seed("notifications", "n4", {"targetUid": "someone-else", "read": False})
        r = _client(app_module).post("/api/notifications/read-all")
        assert r.status_code == 200
        assert r.get_json()["updated"] == 2
        dump = fake_db.dump("notifications")
        assert dump["n1"]["read"] is True
        assert dump["n2"]["read"] is True
        assert dump["n4"]["read"] is False


class TestAdminAnnouncements:
    def test_non_admin_is_rejected(self, fake_db, app_module, monkeypatch):
        require_admin_denied(app_module, monkeypatch)
        r = _client(app_module).post("/api/admin/announcements", json={"title": "x", "message": "y"})
        assert r.status_code == 403

    def test_missing_fields_is_rejected(self, fake_db, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/admin/announcements", json={"title": "", "message": ""})
        assert r.status_code == 400

    def test_too_long_is_rejected(self, fake_db, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/admin/announcements", json={"title": "x" * 200, "message": "y"})
        assert r.status_code == 400

    def test_happy_path_creates_notification_and_audit_log(self, fake_db, app_module, monkeypatch):
        monkeypatch.delenv("ENABLE_FCM_PUSH", raising=False)
        require_admin_as(app_module, monkeypatch, uid="admin-9")
        r = _client(app_module).post("/api/admin/announcements",
                                      json={"title": "Maintenance", "message": "Site down tonight."})
        assert r.status_code == 201
        body = r.get_json()
        assert body["push"] == {"sent": 0, "failed": 0}
        notif = fake_db.dump("notifications")[body["notificationId"]]
        assert notif["targetUid"] == "all"
        assert notif["title"] == "Maintenance"
        audit = list(fake_db.dump("adminAuditLogs").values())
        assert any(a["action"] == "announcement_created" for a in audit)

    def test_announcement_can_target_a_single_user(self, fake_db, app_module, monkeypatch):
        monkeypatch.delenv("ENABLE_FCM_PUSH", raising=False)
        require_admin_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/admin/announcements",
                                      json={"title": "Hi", "message": "Personal note.", "targetUid": "user-1"})
        assert r.status_code == 201
        notif = fake_db.dump("notifications")[r.get_json()["notificationId"]]
        assert notif["targetUid"] == "user-1"

    def test_fcm_push_is_sent_when_enabled(self, fake_db, app_module, monkeypatch, fake_messaging):
        monkeypatch.setenv("ENABLE_FCM_PUSH", "1")
        require_admin_as(app_module, monkeypatch)
        fake_db.seed("fcmTokens", "t1", {"token": "device-token-1", "uid": "user-1"})
        r = _client(app_module).post("/api/admin/announcements",
                                      json={"title": "Push me", "message": "Hello."})
        assert r.status_code == 201
        assert r.get_json()["push"] == {"sent": 1, "failed": 0}

    def test_fcm_push_skips_users_who_opted_out_of_announcements(self, fake_db, app_module, monkeypatch, fake_messaging):
        monkeypatch.setenv("ENABLE_FCM_PUSH", "1")
        require_admin_as(app_module, monkeypatch)
        fake_db.seed("fcmTokens", "t1", {"token": "device-token-1", "uid": "user-1"})
        fake_db.seed("notificationPreferences", "user-1", {"announcements": False})
        r = _client(app_module).post("/api/admin/announcements",
                                      json={"title": "Push me", "message": "Hello."})
        assert r.status_code == 201
        assert r.get_json()["push"] == {"sent": 0, "failed": 0}


class TestSendNotification:
    def test_non_admin_is_rejected(self, fake_db, app_module, monkeypatch, fake_messaging):
        require_admin_denied(app_module, monkeypatch)
        r = _client(app_module).post("/api/notifications/send", json={"tokens": ["t1"], "message": "hi"})
        assert r.status_code == 403

    def test_missing_message_is_rejected(self, fake_db, app_module, monkeypatch, fake_messaging):
        require_admin_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/notifications/send", json={"tokens": ["t1"], "message": ""})
        assert r.status_code == 400

    def test_happy_path_sends_to_explicit_tokens(self, fake_db, app_module, monkeypatch, fake_messaging):
        require_admin_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/notifications/send",
                                      json={"tokens": ["tok-a", "tok-b"], "message": "Hello", "title": "Hi"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["sent"] == 2
        assert body["failed"] == 0

    def test_target_all_pulls_tokens_from_firestore(self, fake_db, app_module, monkeypatch, fake_messaging):
        require_admin_as(app_module, monkeypatch)
        fake_db.seed("fcmTokens", "t1", {"token": "tok-a"})
        fake_db.seed("fcmTokens", "t2", {"token": "tok-b"})
        r = _client(app_module).post("/api/notifications/send", json={"target": "all", "message": "Hello"})
        assert r.status_code == 200
        assert r.get_json()["sent"] == 2

    def test_invalid_tokens_are_removed_and_reported(self, fake_db, app_module, monkeypatch, fake_messaging):
        require_admin_as(app_module, monkeypatch)
        bad_token = "stale-token"
        token_id = hashlib.sha256(bad_token.encode("utf-8")).hexdigest()
        fake_db.seed("fcmTokens", token_id, {"token": bad_token})
        fake_messaging.outcomes = [Exception("registration-token-not-registered")]
        r = _client(app_module).post("/api/notifications/send", json={"tokens": [bad_token], "message": "Hello"})
        assert r.status_code == 502
        body = r.get_json()
        assert body["failed"] == 1
        assert token_id not in fake_db.dump("fcmTokens")

    def test_too_many_tokens_is_rejected(self, fake_db, app_module, monkeypatch, fake_messaging):
        require_admin_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/notifications/send",
                                      json={"tokens": [f"t{i}" for i in range(501)], "message": "Hello"})
        assert r.status_code == 400
