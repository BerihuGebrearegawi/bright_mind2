"""Real behavioural tests for telegram_challenge_routes.py: webhook auth,
server-side identity mapping (a Telegram account must be linked to an
*approved student* account - never trusted from the client), and the
published/grade/region filtering used by /challenges."""
import datetime

import pytest

from telegram_challenge_routes import register_telegram_challenge_routes
from tests.conftest import make_app

WEBHOOK = "/api/telegram/webhook"
SECRET = "test-challenge-secret"


@pytest.fixture
def client(firebase_configured):
    app = make_app(register_telegram_challenge_routes, firebase_configured)
    return app.test_client()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")


def _post(client, body, secret=SECRET):
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret is not None else {}
    return client.post(WEBHOOK, json=body, headers=headers)


def _message(chat_id, tg_user_id, text):
    return {"message": {"chat": {"id": chat_id}, "from": {"id": tg_user_id}, "text": text}}


class TestWebhookAuth:
    def test_missing_secret_rejected(self, client):
        assert _post(client, _message(1, 1, "/start"), secret=None).status_code == 401

    def test_wrong_secret_rejected(self, client):
        assert _post(client, _message(1, 1, "/start"), secret="wrong").status_code == 401


class TestIdentityMapping:
    def test_unlinked_telegram_account_is_told_to_link(self, client, telegram_api, fake_db):
        _post(client, _message(1, 999, "/start"))
        sent = telegram_api.calls_for("sendMessage")
        assert "not linked" in sent[0]["text"]

    def test_non_student_account_is_not_treated_as_a_student(self, client, telegram_api, fake_db):
        # A teacher's telegramUserId must never be accepted here - this bot is
        # student-only; teacher/admin publishing goes through the separate bot.
        fake_db.seed("users", "teacher-1", {"telegramUserId": "42", "accountType": "teacher"})
        _post(client, _message(1, 42, "/start"))
        sent = telegram_api.calls_for("sendMessage")
        assert "not linked" in sent[0]["text"]

    def test_linked_student_gets_the_start_menu(self, client, telegram_api, fake_db):
        fake_db.seed("users", "student-1", {"telegramUserId": "77", "accountType": "student", "className": "9"})
        _post(client, _message(1, 77, "/start"))
        sent = telegram_api.calls_for("sendMessage")
        assert "Academic Challenge" in sent[0]["text"]


class TestChallengesListing:
    def _seed_student(self, fake_db, tg_id="77", grade="9", region=""):
        fake_db.seed("users", "student-1", {
            "telegramUserId": tg_id, "accountType": "student", "className": grade, "region": region,
        })

    def test_no_active_challenges_message(self, client, telegram_api, fake_db):
        self._seed_student(fake_db)
        _post(client, _message(1, 77, "/challenges"))
        sent = telegram_api.calls_for("sendMessage")
        assert "No active challenges" in sent[0]["text"]

    def test_published_challenge_matching_grade_is_listed(self, client, telegram_api, fake_db):
        self._seed_student(fake_db, grade="9")
        fake_db.seed("academicChallenges", "c1", {
            "status": "published", "grade": "9", "title": "Math Sprint", "region": "ALL",
        })
        _post(client, _message(1, 77, "/challenges"))
        sent = telegram_api.calls_for("sendMessage")
        assert "Math Sprint" in sent[0]["text"]

    def test_challenge_for_a_different_grade_is_excluded(self, client, telegram_api, fake_db):
        self._seed_student(fake_db, grade="9")
        fake_db.seed("academicChallenges", "c1", {
            "status": "published", "grade": "5", "title": "Grade 5 Only", "region": "ALL",
        })
        _post(client, _message(1, 77, "/challenges"))
        sent = telegram_api.calls_for("sendMessage")
        assert "No active challenges" in sent[0]["text"]

    def test_unpublished_challenge_is_excluded(self, client, telegram_api, fake_db):
        self._seed_student(fake_db, grade="9")
        fake_db.seed("academicChallenges", "c1", {
            "status": "draft", "grade": "9", "title": "Draft Challenge", "region": "ALL",
        })
        _post(client, _message(1, 77, "/challenges"))
        sent = telegram_api.calls_for("sendMessage")
        assert "No active challenges" in sent[0]["text"]

    def test_expired_challenge_is_excluded(self, client, telegram_api, fake_db):
        self._seed_student(fake_db, grade="9")
        yesterday = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).isoformat()
        fake_db.seed("academicChallenges", "c1", {
            "status": "published", "grade": "9", "title": "Expired", "region": "ALL", "endsAt": yesterday,
        })
        _post(client, _message(1, 77, "/challenges"))
        sent = telegram_api.calls_for("sendMessage")
        assert "No active challenges" in sent[0]["text"]
