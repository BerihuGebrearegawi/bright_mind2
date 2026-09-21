"""Real behavioural tests for app.py's admin Telegram bot control panel -
the inline routes that let an admin check bot status and register the
webhook for each of BMT's three independent Telegram bots (the Academic
Challenge bot's routes live directly on app.py; the Moderator and
Publisher bots share the same two helper functions, `_telegram_bot_status`
and `_telegram_bot_set_webhook`, parameterized by env var names and the
webhook path).

This is separate from telegram_challenge_routes.py /
telegram_moderator_bot_routes.py / telegram_publisher_bot_routes.py /
telegram_platform_routes.py (already covered by their own dedicated test
files) - those handle each bot's actual inbound webhook traffic; this file
covers the *admin-facing* status/configuration routes for all three, which
are inline in app.py itself and were not previously tested.
"""
import pytest

from tests.fakes import FakeTelegramBotAdminAPI

# (status_path, set_webhook_path, token_env, secret_env, required_webhook_path)
BOTS = [
    pytest.param("/api/admin/telegram/status", "/api/admin/telegram/set-webhook",
                 "TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "/api/telegram/webhook",
                 id="academic"),
    pytest.param("/api/admin/telegram/moderator/status", "/api/admin/telegram/moderator/set-webhook",
                 "TELEGRAM_MODERATOR_BOT_TOKEN", "TELEGRAM_MODERATOR_WEBHOOK_SECRET",
                 "/api/telegram/moderator/webhook", id="moderator"),
    pytest.param("/api/admin/telegram/publisher/status", "/api/admin/telegram/publisher/set-webhook",
                 "TELEGRAM_PUBLISHER_BOT_TOKEN", "TELEGRAM_PUBLISHER_WEBHOOK_SECRET",
                 "/api/telegram/publisher/webhook", id="publisher"),
]


def require_admin_as(app_module, monkeypatch, uid="admin-1"):
    monkeypatch.setattr(app_module, "_require_admin_bearer", lambda: (True, {"uid": uid, "admin": True}))


def require_admin_denied(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "_require_admin_bearer",
                         lambda: (False, ({"error": "Admin access required."}, 403)))


def _client(app_module):
    return app_module.app.test_client()


def _patch_telegram(monkeypatch, **kwargs):
    import requests as requests_module
    fake = FakeTelegramBotAdminAPI(**kwargs)
    monkeypatch.setattr(requests_module, "get", fake.get)
    monkeypatch.setattr(requests_module, "post", fake.post)
    return fake


@pytest.mark.parametrize("status_path,webhook_path,token_env,secret_env,required_path", BOTS)
class TestTelegramAdminControlPanel:
    def test_status_requires_admin(self, fake_db, app_module, monkeypatch,
                                    status_path, webhook_path, token_env, secret_env, required_path):
        require_admin_denied(app_module, monkeypatch)
        r = _client(app_module).get(status_path)
        assert r.status_code == 403

    def test_status_reports_unconfigured_when_no_token(self, fake_db, app_module, monkeypatch,
                                                         status_path, webhook_path, token_env, secret_env, required_path):
        monkeypatch.delenv(token_env, raising=False)
        require_admin_as(app_module, monkeypatch)
        r = _client(app_module).get(status_path)
        assert r.status_code == 200
        body = r.get_json()
        assert body["configured"] is False
        assert body["webhookConfigured"] is False

    def test_status_happy_path(self, fake_db, app_module, monkeypatch,
                                status_path, webhook_path, token_env, secret_env, required_path):
        monkeypatch.setenv(token_env, "123:abc")
        monkeypatch.setenv(secret_env, "whsec")
        require_admin_as(app_module, monkeypatch)
        _patch_telegram(monkeypatch, bot_name="BMT Bot", username="bmt_bot",
                         webhook_url=f"https://bmt.example{required_path}", pending_updates=2)
        r = _client(app_module).get(status_path)
        assert r.status_code == 200
        body = r.get_json()
        assert body["configured"] is True
        assert body["username"] == "bmt_bot"
        assert body["webhookConfigured"] is True
        assert body["webhookPendingUpdates"] == 2
        assert body["secretConfigured"] is True

    def test_status_telegram_unreachable_returns_503(self, fake_db, app_module, monkeypatch,
                                                       status_path, webhook_path, token_env, secret_env, required_path):
        monkeypatch.setenv(token_env, "123:abc")
        require_admin_as(app_module, monkeypatch)
        _patch_telegram(monkeypatch, raise_connection_error=True)
        r = _client(app_module).get(status_path)
        assert r.status_code == 503

    def test_set_webhook_requires_admin(self, fake_db, app_module, monkeypatch,
                                         status_path, webhook_path, token_env, secret_env, required_path):
        require_admin_denied(app_module, monkeypatch)
        r = _client(app_module).post(webhook_path, json={})
        assert r.status_code == 403

    def test_set_webhook_requires_token_and_secret_configured(self, fake_db, app_module, monkeypatch,
                                                                status_path, webhook_path, token_env, secret_env, required_path):
        monkeypatch.delenv(token_env, raising=False)
        monkeypatch.delenv(secret_env, raising=False)
        require_admin_as(app_module, monkeypatch)
        r = _client(app_module).post(webhook_path, json={})
        assert r.status_code == 503

    def test_set_webhook_rejects_wrong_path(self, fake_db, app_module, monkeypatch,
                                             status_path, webhook_path, token_env, secret_env, required_path):
        monkeypatch.setenv(token_env, "123:abc")
        monkeypatch.setenv(secret_env, "whsec")
        require_admin_as(app_module, monkeypatch)
        r = _client(app_module).post(webhook_path, json={"url": "https://bmt.example/wrong-path"})
        assert r.status_code == 400

    def test_set_webhook_rejects_non_https_supplied_url(self, fake_db, app_module, monkeypatch,
                                                          status_path, webhook_path, token_env, secret_env, required_path):
        monkeypatch.setenv(token_env, "123:abc")
        monkeypatch.setenv(secret_env, "whsec")
        require_admin_as(app_module, monkeypatch)
        r = _client(app_module).post(webhook_path, json={"url": f"http://bmt.example{required_path}"})
        assert r.status_code == 400

    def test_set_webhook_without_explicit_url_requires_https_request_root(
            self, fake_db, app_module, monkeypatch, status_path, webhook_path, token_env, secret_env, required_path):
        """No `url` in the body -> app.py derives it from request.url_root,
        which is plain http:// under the Flask test client -> rejected."""
        monkeypatch.setenv(token_env, "123:abc")
        monkeypatch.setenv(secret_env, "whsec")
        require_admin_as(app_module, monkeypatch)
        r = _client(app_module).post(webhook_path, json={})
        assert r.status_code == 400

    def test_set_webhook_telegram_rejection_returns_502(self, fake_db, app_module, monkeypatch,
                                                          status_path, webhook_path, token_env, secret_env, required_path):
        monkeypatch.setenv(token_env, "123:abc")
        monkeypatch.setenv(secret_env, "whsec")
        require_admin_as(app_module, monkeypatch)
        _patch_telegram(monkeypatch, set_webhook_ok=False, set_webhook_description="Bad webhook host")
        r = _client(app_module).post(webhook_path, json={"url": f"https://bmt.example{required_path}"})
        assert r.status_code == 502
        assert "Bad webhook host" in r.get_json()["telegramError"]

    def test_set_webhook_happy_path(self, fake_db, app_module, monkeypatch,
                                     status_path, webhook_path, token_env, secret_env, required_path):
        monkeypatch.setenv(token_env, "123:abc")
        monkeypatch.setenv(secret_env, "whsec")
        require_admin_as(app_module, monkeypatch)
        fake = _patch_telegram(monkeypatch, set_webhook_ok=True)
        target_url = f"https://bmt.example{required_path}"
        r = _client(app_module).post(webhook_path, json={"url": target_url})
        assert r.status_code == 200
        assert r.get_json()["webhookUrl"] == target_url
        sent = [c for c in fake.calls if c["method"] == "POST"][0]
        assert sent["json"]["url"] == target_url
        assert sent["json"]["secret_token"] == "whsec"

    def test_set_webhook_telegram_unreachable_returns_503(self, fake_db, app_module, monkeypatch,
                                                            status_path, webhook_path, token_env, secret_env, required_path):
        monkeypatch.setenv(token_env, "123:abc")
        monkeypatch.setenv(secret_env, "whsec")
        require_admin_as(app_module, monkeypatch)
        _patch_telegram(monkeypatch, raise_connection_error=True)
        r = _client(app_module).post(webhook_path, json={"url": f"https://bmt.example{required_path}"})
        assert r.status_code == 503
