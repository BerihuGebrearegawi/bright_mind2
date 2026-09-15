"""Real behavioural tests for telegram_publisher_bot_routes.py: staff-only
access, target selection + copy-message publishing flow, and the web-dashboard
quiz-poll distribution endpoint."""
import pytest

from telegram_publisher_bot_routes import register_telegram_publisher_bot_routes
from telegram_targets_config import BMT_TELEGRAM_TARGETS
from tests.conftest import make_app

WEBHOOK = "/api/telegram/publisher/webhook"
SECRET = "test-publisher-secret"


def fake_require_user(uid="staff-1"):
    return lambda: (True, {"uid": uid})


@pytest.fixture
def client(firebase_configured):
    app = make_app(register_telegram_publisher_bot_routes, firebase_configured, fake_require_user())
    return app.test_client()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_PUBLISHER_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("TELEGRAM_PUBLISHER_BOT_TOKEN", "test-token")


def _post(client, body, secret=SECRET):
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret is not None else {}
    return client.post(WEBHOOK, json=body, headers=headers)


def _seed_admin(fake_db, tg_id, uid="admin-1"):
    fake_db.seed("users", uid, {"telegramUserId": str(tg_id), "accountType": "admin"})


def _seed_approved_teacher(fake_db, tg_id, uid="teacher-1"):
    fake_db.seed("users", uid, {"telegramUserId": str(tg_id), "accountType": "teacher"})
    fake_db.seed("teachers", uid, {"approved": True})


class TestWebhookAuth:
    def test_wrong_secret_rejected(self, client):
        r = _post(client, {"message": {"chat": {"id": 1}, "from": {"id": 1}, "text": "/start"}}, secret="wrong")
        assert r.status_code == 401


class TestStaffGate:
    def test_unknown_telegram_user_is_rejected(self, client, telegram_api, fake_db):
        _post(client, {"message": {"chat": {"id": 1}, "from": {"id": 999}, "text": "/start"}})
        sent = telegram_api.calls_for("sendMessage")
        assert len(sent) == 1
        assert "restricted" in sent[0]["text"]

    def test_unapproved_teacher_is_rejected(self, client, telegram_api, fake_db):
        fake_db.seed("users", "t1", {"telegramUserId": "42", "accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": False})
        _post(client, {"message": {"chat": {"id": 1}, "from": {"id": 42}, "text": "/start"}})
        sent = telegram_api.calls_for("sendMessage")
        assert "restricted" in sent[0]["text"]

    def test_admin_can_use_start(self, client, telegram_api, fake_db):
        _seed_admin(fake_db, 100)
        _post(client, {"message": {"chat": {"id": 1}, "from": {"id": 100}, "text": "/start"}})
        sent = telegram_api.calls_for("sendMessage")
        assert "Publisher Bot" in sent[0]["text"]


class TestTargetListing:
    def test_targets_includes_canonical_bmt_channels(self, client, telegram_api, fake_db):
        _seed_admin(fake_db, 100)
        _post(client, {"message": {"chat": {"id": 1}, "from": {"id": 100}, "text": "/targets"}})
        sent = telegram_api.calls_for("sendMessage")
        buttons = sent[-1]["reply_markup"]["inline_keyboard"]
        names = [b["text"] for row in buttons for b in row]
        expected_names = {t["name"] for t in BMT_TELEGRAM_TARGETS}
        assert expected_names.issubset(set(names))
        assert len(names) >= 12  # 12 canonical targets, plus any custom ones


class TestPublishFlow:
    def test_publish_by_target_id_then_forward_copies_message(self, client, telegram_api, fake_db):
        _seed_approved_teacher(fake_db, 200)
        r1 = _post(client, {"message": {"chat": {"id": 1}, "from": {"id": 200}, "text": "/publish general"}})
        assert r1.status_code == 200
        select_msg = telegram_api.calls_for("sendMessage")[-1]
        assert "Target selected" in select_msg["text"]

        r2 = _post(client, {"message": {"chat": {"id": 1}, "from": {"id": 200}, "text": "Announcement text", "message_id": 55}})
        assert r2.status_code == 200
        copies = telegram_api.calls_for("copyMessage")
        assert len(copies) == 1
        assert copies[0]["from_chat_id"] == 1
        assert copies[0]["message_id"] == 55
        assert copies[0]["chat_id"] == "-1002244140012"  # 'general' target chatId

        posts = fake_db.dump("telegramPosts")
        assert len(posts) == 1
        record = next(iter(posts.values()))
        assert record["source"] == "publisher_bot"
        assert record["targetId"] == "general"
        assert record["successCount"] == 1

    def test_publishing_without_selecting_target_first_is_rejected(self, client, telegram_api, fake_db):
        _seed_approved_teacher(fake_db, 200)
        _post(client, {"message": {"chat": {"id": 1}, "from": {"id": 200}, "text": "Some content", "message_id": 1}})
        assert telegram_api.calls_for("copyMessage") == []
        sent = telegram_api.calls_for("sendMessage")
        assert "Select a target first" in sent[-1]["text"]

    def test_unknown_target_id_is_rejected(self, client, telegram_api, fake_db):
        _seed_admin(fake_db, 100)
        _post(client, {"message": {"chat": {"id": 1}, "from": {"id": 100}, "text": "/publish nonexistent_target"}})
        sent = telegram_api.calls_for("sendMessage")
        assert "Unknown target" in sent[-1]["text"]

    def test_cancel_clears_selected_target(self, client, telegram_api, fake_db):
        _seed_admin(fake_db, 100)
        _post(client, {"message": {"chat": {"id": 1}, "from": {"id": 100}, "text": "/publish general"}})
        _post(client, {"message": {"chat": {"id": 1}, "from": {"id": 100}, "text": "/cancel"}})
        _post(client, {"message": {"chat": {"id": 1}, "from": {"id": 100}, "text": "content", "message_id": 9}})
        assert telegram_api.calls_for("copyMessage") == []


class TestSendQuizEndpoint:
    def test_requires_authenticated_web_user(self, monkeypatch):
        app = make_app(register_telegram_publisher_bot_routes, lambda: True, None)
        client = app.test_client()
        r = client.post("/api/telegram/publisher/send-quiz", json={})
        assert r.status_code == 503  # require_user not wired -> feature unavailable

    def test_sends_approved_question_as_quiz_poll(self, fake_db, telegram_api):
        app = make_app(register_telegram_publisher_bot_routes, lambda: True, fake_require_user("admin-1"))
        client = app.test_client()
        fake_db.seed("users", "admin-1", {"accountType": "admin"})
        fake_db.seed("questionBank", "q1", {
            "status": "approved",
            "question": "2 + 2 = ?",
            "options": {"A": "3", "B": "4", "C": "5"},
            "correctAnswer": "B",
        })
        r = client.post(
            "/api/telegram/publisher/send-quiz",
            json={"targetId": "general", "questionIds": ["q1"]},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["sentCount"] == 1
        polls = telegram_api.calls_for("sendPoll")
        assert len(polls) == 1
        assert polls[0]["correct_option_id"] == 1  # index of "B" among sorted ["A","B","C"]

    def test_rejects_unapproved_question(self, fake_db, telegram_api):
        app = make_app(register_telegram_publisher_bot_routes, lambda: True, fake_require_user("admin-1"))
        client = app.test_client()
        fake_db.seed("users", "admin-1", {"accountType": "admin"})
        fake_db.seed("questionBank", "q2", {"status": "pending", "options": {"A": "x", "B": "y"}, "correctAnswer": "A"})
        r = client.post(
            "/api/telegram/publisher/send-quiz",
            json={"targetId": "general", "questionIds": ["q2"]},
        )
        assert r.status_code == 502
        body = r.get_json()
        assert body["failed"][0]["reason"] == "not_approved"
