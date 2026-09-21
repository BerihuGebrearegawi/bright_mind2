"""Real behavioural tests for telegram_platform_routes.py: role gating on the
admin/teacher publishing endpoints, smart target selection, publish/schedule
flows, and the scheduler-tick secret."""
import pytest

from telegram_platform_routes import register_telegram_platform_routes
from tests.conftest import make_app

SCHEDULER_SECRET = "test-scheduler-secret"


def require_user_as(uid, role=None, admin=False):
    return lambda: (True, {"uid": uid, "role": role, "admin": admin})


def require_admin_ok():
    return lambda: (True, {})


def require_admin_denied():
    return lambda: (False, ({"error": "Admin access required."}, 403))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("BMT_SCHEDULER_SECRET", SCHEDULER_SECRET)


def _client(require_user, require_admin, firebase_configured=True):
    factory = (lambda: True) if firebase_configured else (lambda: None)
    app = make_app(register_telegram_platform_routes, require_user, require_admin, factory)
    return app.test_client()


class TestRoleGating:
    def test_non_staff_user_cannot_list_targets(self, fake_db):
        client = _client(require_user_as("student-1"), require_admin_denied())
        r = client.get("/api/admin/telegram/targets")
        assert r.status_code == 403

    def test_unapproved_teacher_cannot_list_targets(self, fake_db):
        fake_db.seed("teachers", "t1", {"approved": False})
        client = _client(require_user_as("t1"), require_admin_denied())
        r = client.get("/api/admin/telegram/targets")
        assert r.status_code == 403

    def test_approved_teacher_can_list_targets(self, fake_db):
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(require_user_as("t1"), require_admin_denied())
        r = client.get("/api/admin/telegram/targets")
        assert r.status_code == 200
        assert len(r.get_json()["targets"]) >= 12

    def test_admin_can_list_targets(self, fake_db):
        client = _client(require_user_as("a1", admin=True), require_admin_denied())
        r = client.get("/api/admin/telegram/targets")
        assert r.status_code == 200

    def test_scheduling_requires_true_admin_not_just_teacher(self, fake_db):
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(require_user_as("t1"), require_admin_denied())
        r = client.post("/api/admin/telegram/schedules", json={
            "targetChatIds": ["general"], "type": "text", "text": "hi", "runAt": "2030-01-01T00:00:00+00:00"
        })
        assert r.status_code == 403


class TestSmartTargets:
    def test_grade_filter_returns_matching_grade_channels_and_groups(self, fake_db):
        client = _client(require_user_as("a1", admin=True), require_admin_ok())
        r = client.get("/api/admin/telegram/smart-targets?grade=9")
        assert r.status_code == 200
        ids = {t["id"] for t in r.get_json()["targets"]}
        assert "grade_9_10_channel" in ids
        assert "grade_9_10_group" in ids
        assert "grade_5_6_channel" not in ids

    def test_academic_purpose_includes_academic_channel_regardless_of_grade(self, fake_db):
        client = _client(require_user_as("a1", admin=True), require_admin_ok())
        r = client.get("/api/admin/telegram/smart-targets?purpose=academic")
        ids = {t["id"] for t in r.get_json()["targets"]}
        assert "academic_challenge" in ids


class TestPublish:
    def test_publish_text_to_multiple_targets_logs_post(self, fake_db, telegram_api):
        client = _client(require_user_as("a1", admin=True), require_admin_ok())
        r = client.post("/api/admin/telegram/publish", json={
            "targetChatIds": ["-1002244140012", "-1003534125070"],
            "type": "text",
            "text": "Hello students!",
        })
        assert r.status_code == 201
        body = r.get_json()
        assert body["success"] is True
        assert len(body["results"]) == 2
        sent = telegram_api.calls_for("sendMessage")
        assert len(sent) == 2
        posts = fake_db.dump("telegramPosts")
        assert len(posts) == 1
        record = next(iter(posts.values()))
        assert record["successCount"] == 2

    def test_publish_rejects_more_than_six_targets(self, fake_db):
        client = _client(require_user_as("a1", admin=True), require_admin_ok())
        r = client.post("/api/admin/telegram/publish", json={
            "targetChatIds": [str(i) for i in range(7)],
            "type": "text",
            "text": "hi",
        })
        assert r.status_code == 400

    def test_publish_unsupported_type_rejected(self, fake_db):
        client = _client(require_user_as("a1", admin=True), require_admin_ok())
        r = client.post("/api/admin/telegram/publish", json={
            "targetChatIds": ["1"], "type": "sticker", "text": "hi",
        })
        assert r.status_code == 400

    def test_publish_poll_requires_at_least_two_options(self, fake_db, telegram_api):
        client = _client(require_user_as("a1", admin=True), require_admin_ok())
        r = client.post("/api/admin/telegram/publish", json={
            "targetChatIds": ["1"], "type": "poll", "text": "Pick one", "options": ["only-one"],
        })
        # single target failing -> success=0 -> 502
        assert r.status_code == 502
        assert telegram_api.calls_for("sendPoll") == []


class TestSchedulerTick:
    def test_wrong_secret_rejected(self, fake_db):
        client = _client(require_user_as("a1", admin=True), require_admin_ok())
        r = client.post("/api/admin/telegram/scheduler/tick", headers={"X-BMT-SCHEDULER-SECRET": "wrong"})
        assert r.status_code == 401

    def test_due_schedule_is_sent_and_marked_sent(self, fake_db, telegram_api):
        import datetime
        client = _client(require_user_as("a1", admin=True), require_admin_ok())
        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
        fake_db.seed("telegramSchedules", "sched1", {
            "status": "scheduled",
            "runAt": past,
            "targetChatIds": ["1"],
            "content": {"type": "text", "text": "Scheduled post"},
        })
        r = client.post("/api/admin/telegram/scheduler/tick", headers={"X-BMT-SCHEDULER-SECRET": SCHEDULER_SECRET})
        assert r.status_code == 200
        assert r.get_json()["processed"] == 1
        assert fake_db.dump("telegramSchedules")["sched1"]["status"] == "sent"
        assert len(telegram_api.calls_for("sendMessage")) == 1

    def test_future_schedule_is_left_untouched(self, fake_db, telegram_api):
        import datetime
        client = _client(require_user_as("a1", admin=True), require_admin_ok())
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
        fake_db.seed("telegramSchedules", "sched2", {
            "status": "scheduled", "runAt": future, "targetChatIds": ["1"],
            "content": {"type": "text", "text": "Future post"},
        })
        r = client.post("/api/admin/telegram/scheduler/tick", headers={"X-BMT-SCHEDULER-SECRET": SCHEDULER_SECRET})
        assert r.get_json()["processed"] == 0
        assert telegram_api.calls_for("sendMessage") == []


class TestModerationLogsReadback:
    def test_admin_can_read_moderation_logs(self, fake_db):
        fake_db.seed("telegramModerationLogs", "log1", {
            "chatId": "-100", "userId": "5", "bot": "moderator", "action": "deleted", "reason": "spam",
        })
        client = _client(require_user_as("a1", admin=True), require_admin_ok())
        r = client.get("/api/admin/telegram/moderation-logs")
        assert r.status_code == 200
        assert len(r.get_json()["logs"]) == 1
