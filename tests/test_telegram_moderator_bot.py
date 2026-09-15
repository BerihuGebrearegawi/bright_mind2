"""Real behavioural tests for telegram_moderator_bot_routes.py, replacing the
old string-matching regression scripts with assertions on actual request/
response behaviour: webhook auth, spam detection, and the delete -> mute ->
ban escalation path."""
import pytest

from telegram_moderator_bot_routes import register_telegram_moderator_bot_routes
from tests.conftest import make_app

WEBHOOK = "/api/telegram/moderator/webhook"
SECRET = "test-moderator-secret"


@pytest.fixture
def client(firebase_configured):
    app = make_app(register_telegram_moderator_bot_routes, firebase_configured)
    return app.test_client()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_MODERATOR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("TELEGRAM_MODERATOR_BOT_TOKEN", "test-token")


def _post(client, body, secret=SECRET):
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret is not None else {}
    return client.post(WEBHOOK, json=body, headers=headers)


class TestWebhookAuth:
    def test_missing_secret_header_is_rejected(self, client):
        r = _post(client, {"message": {"chat": {"id": 1, "type": "private"}, "text": "/start"}}, secret=None)
        assert r.status_code == 401

    def test_wrong_secret_is_rejected(self, client):
        r = _post(client, {"message": {"chat": {"id": 1, "type": "private"}, "text": "/start"}}, secret="wrong")
        assert r.status_code == 401

    def test_correct_secret_is_accepted(self, client, telegram_api):
        r = _post(client, {"message": {"chat": {"id": 1, "type": "private"}, "text": "/start"}})
        assert r.status_code == 200


class TestStartCommand:
    def test_start_sends_menu_with_rules_and_contact_buttons(self, client, telegram_api, fake_db):
        _post(client, {"message": {"chat": {"id": 555, "type": "private"}, "text": "/start"}})
        sent = telegram_api.calls_for("sendMessage")
        assert len(sent) == 1
        assert sent[0]["chat_id"] == 555
        buttons = sent[0]["reply_markup"]["inline_keyboard"]
        button_texts = [b["text"] for row in buttons for b in row]
        assert "📜 Group Rules" in button_texts
        assert "📞 Contact Support" in button_texts


class TestSpamDetection:
    def _group_message(self, chat_id, user_id, text, message_id=1):
        return {
            "message": {
                "chat": {"id": chat_id, "type": "supergroup"},
                "from": {"id": user_id},
                "text": text,
                "message_id": message_id,
            }
        }

    def test_clean_message_is_left_alone(self, client, telegram_api, fake_db):
        _post(client, self._group_message(-100, 1, "Hello everyone, how is the homework going?"))
        assert telegram_api.calls_for("deleteMessage") == []
        assert fake_db.count("telegramModerationLogs") == 0

    def test_suspicious_phrase_is_deleted_and_logged(self, client, telegram_api, fake_db):
        _post(client, self._group_message(-100, 1, "You are a guaranteed profit winner! verify your wallet now"))
        assert len(telegram_api.calls_for("deleteMessage")) == 1
        logs = fake_db.dump("telegramModerationLogs")
        assert len(logs) == 1
        entry = next(iter(logs.values()))
        assert entry["action"] == "deleted"
        assert entry["reason"] == "suspicious_link_or_spam"
        assert entry["userId"] == "1"

    def test_excessive_links_are_flagged_as_spam(self, client, telegram_api, fake_db):
        spammy = "check these out http://a.co http://b.co http://c.co http://d.co"
        _post(client, self._group_message(-100, 2, spammy))
        logs = fake_db.dump("telegramModerationLogs")
        entry = next(iter(logs.values()))
        assert entry["reason"] == "suspicious_link_or_spam"

    def test_excessive_mentions_are_flagged(self, client, telegram_api, fake_db):
        mentiony = " ".join(f"@user{i}" for i in range(9))
        _post(client, self._group_message(-100, 3, mentiony))
        logs = fake_db.dump("telegramModerationLogs")
        entry = next(iter(logs.values()))
        assert entry["reason"] == "excessive_mentions"


class TestEscalation:
    """delete -> (>=2 prior violations) mute 1h -> (>=5 prior violations) ban."""

    def _seed_prior_violations(self, fake_db, chat_id, user_id, count, action="deleted"):
        for i in range(count):
            fake_db.seed(
                "telegramModerationLogs",
                f"prior_{i}",
                {"chatId": str(chat_id), "userId": str(user_id), "action": action, "reason": "suspicious_link_or_spam"},
            )

    def test_first_violation_only_deletes(self, client, telegram_api, fake_db):
        _post(client, {
            "message": {"chat": {"id": -200, "type": "group"}, "from": {"id": 10},
                        "text": "free crypto giveaway winner", "message_id": 1}
        })
        assert telegram_api.calls_for("restrictChatMember") == []
        assert telegram_api.calls_for("banChatMember") == []
        assert len(telegram_api.calls_for("deleteMessage")) == 1

    def test_repeated_violations_trigger_a_mute(self, client, telegram_api, fake_db):
        self._seed_prior_violations(fake_db, -200, 10, count=2)
        _post(client, {
            "message": {"chat": {"id": -200, "type": "group"}, "from": {"id": 10},
                        "text": "free crypto giveaway winner", "message_id": 2}
        })
        assert len(telegram_api.calls_for("restrictChatMember")) == 1
        assert telegram_api.calls_for("banChatMember") == []

    def test_persistent_offenders_get_banned(self, client, telegram_api, fake_db):
        self._seed_prior_violations(fake_db, -200, 10, count=5)
        _post(client, {
            "message": {"chat": {"id": -200, "type": "group"}, "from": {"id": 10},
                        "text": "free crypto giveaway winner", "message_id": 3}
        })
        assert len(telegram_api.calls_for("banChatMember")) == 1


class TestMenuCallbacks:
    def test_rules_callback_sends_rules_text(self, client, telegram_api):
        body = {
            "callback_query": {
                "id": "cb1",
                "data": "menu:rules",
                "message": {"chat": {"id": 777}},
                "from": {"id": 1},
            }
        }
        _post(client, body)
        assert telegram_api.calls_for("answerCallbackQuery")
        sent = telegram_api.calls_for("sendMessage")
        assert any("Group Safety Rules" in s["text"] for s in sent)
