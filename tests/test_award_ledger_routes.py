"""Real behavioural tests for award_ledger_routes.py's close_challenge
endpoint: admin gating, status-transition guards, and the open-attempt
safety check that prevents closing a still-active challenge early.
"""
from datetime import datetime, timedelta, timezone

import pytest

from award_ledger_routes import register_award_ledger_routes
from tests.conftest import make_app


def require_user_as(uid):
    return lambda: (True, {"uid": uid})


def require_user_denied():
    return lambda: (False, ({"error": "Sign in required."}, 401))


def _client(fake_db, require_user, firebase_configured=True):
    factory = (lambda: True) if firebase_configured else (lambda: None)
    app = make_app(register_award_ledger_routes, require_user, factory)
    return app.test_client()


def _seed_challenge(fake_db, cid="c1", status="published", ends_in_minutes=-5, **extra):
    ends_at = datetime.now(timezone.utc) + timedelta(minutes=ends_in_minutes)
    doc = {"status": status, "endsAt": ends_at}
    doc.update(extra)
    fake_db.seed("academicChallenges", cid, doc)
    return doc


def _seed_admin(fake_db, uid="admin-1"):
    fake_db.seed("users", uid, {"accountType": "admin"})


def _seed_student(fake_db, uid="student-1"):
    fake_db.seed("users", uid, {"accountType": "student"})


class TestAuthAndRoleGating:
    def test_requires_sign_in(self, fake_db):
        client = _client(fake_db, require_user_denied())
        r = client.post("/api/admin/challenges/c1/close")
        assert r.status_code == 401

    def test_non_admin_is_rejected(self, fake_db):
        _seed_student(fake_db, "student-1")
        _seed_challenge(fake_db)
        client = _client(fake_db, require_user_as("student-1"))
        r = client.post("/api/admin/challenges/c1/close")
        assert r.status_code == 403

    def test_firebase_not_configured_returns_503(self, fake_db):
        client = _client(fake_db, require_user_as("admin-1"), firebase_configured=False)
        r = client.post("/api/admin/challenges/c1/close")
        assert r.status_code == 503


class TestStatusTransitions:
    def test_missing_challenge_returns_404(self, fake_db):
        _seed_admin(fake_db)
        client = _client(fake_db, require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/does-not-exist/close")
        assert r.status_code == 404

    def test_finalized_challenge_cannot_be_closed_again(self, fake_db):
        _seed_admin(fake_db)
        _seed_challenge(fake_db, status="finalized")
        client = _client(fake_db, require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/c1/close")
        assert r.status_code == 409

    def test_already_closed_is_idempotent(self, fake_db):
        _seed_admin(fake_db)
        _seed_challenge(fake_db, status="closed")
        client = _client(fake_db, require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/c1/close")
        assert r.status_code == 200
        assert r.get_json()["alreadyClosed"] is True

    def test_draft_challenge_cannot_be_closed(self, fake_db):
        _seed_admin(fake_db)
        _seed_challenge(fake_db, status="draft")
        client = _client(fake_db, require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/c1/close")
        assert r.status_code == 409

    def test_successful_close_of_ended_challenge(self, fake_db):
        _seed_admin(fake_db)
        _seed_challenge(fake_db, status="active", ends_in_minutes=-1)
        client = _client(fake_db, require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/c1/close")
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is True
        assert body["status"] == "closed"
        assert fake_db.dump("academicChallenges")["c1"]["status"] == "closed"
        assert fake_db.dump("academicChallenges")["c1"]["closedBy"] == "admin-1"


class TestOpenAttemptSafety:
    def test_cannot_close_early_with_an_open_attempt(self, fake_db):
        """A challenge still within its time window, with a participant mid-attempt,
        must not be closeable: that would strand an attempt that can never submit."""
        _seed_admin(fake_db)
        _seed_challenge(fake_db, status="active", ends_in_minutes=30)
        fake_db.seed("challengeAttempts", "a1", {"challengeId": "c1", "status": "started"})
        client = _client(fake_db, require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/c1/close")
        assert r.status_code == 409

    def test_can_close_early_when_no_open_attempts_remain(self, fake_db):
        _seed_admin(fake_db)
        _seed_challenge(fake_db, status="active", ends_in_minutes=30)
        fake_db.seed("challengeAttempts", "a1", {"challengeId": "c1", "status": "submitted"})
        client = _client(fake_db, require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/c1/close")
        assert r.status_code == 200

    def test_naive_end_time_is_rejected_as_invalid(self, fake_db):
        """endsAt must be timezone-aware; a naive datetime cannot be safely compared."""
        _seed_admin(fake_db)
        naive_end = datetime.now() + timedelta(minutes=30)
        fake_db.seed("academicChallenges", "c1", {"status": "active", "endsAt": naive_end})
        client = _client(fake_db, require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/c1/close")
        assert r.status_code == 500
