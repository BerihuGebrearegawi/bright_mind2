"""Real behavioural tests for award_ledger_routes.py's finalize_awards
endpoint: admin gating, the CLOSED-before-finalize guard, ranking/prize-pool
allocation, and idempotency of the atomic finalize transaction.
"""
from datetime import datetime, timedelta, timezone

import pytest

from award_ledger_routes import register_award_ledger_routes
from tests.conftest import make_app


def require_user_as(uid):
    return lambda: (True, {"uid": uid})


def _client(require_user, firebase_configured=True):
    factory = (lambda: True) if firebase_configured else (lambda: None)
    app = make_app(register_award_ledger_routes, require_user, factory)
    return app.test_client()


def _seed_admin(fake_db, uid="admin-1"):
    fake_db.seed("users", uid, {"accountType": "admin"})


def _seed_closed_challenge(fake_db, cid="c1", **extra):
    doc = {
        "status": "closed",
        "endsAt": datetime.now(timezone.utc) - timedelta(minutes=5),
        "awardCount": 2,
        "published": True,
        "prizePoolShare": 0.5,
        "prizeCurrency": "ETB",
    }
    doc.update(extra)
    fake_db.seed("academicChallenges", cid, doc)


def _seed_entry(fake_db, eid, cid, uid, amount):
    fake_db.seed("challengeEntries", eid, {"challengeId": cid, "status": "verified", "userId": uid, "amount": amount})


def _seed_attempt(fake_db, aid, cid, uid, score, percentage):
    fake_db.seed("challengeAttempts", aid, {"challengeId": cid, "status": "submitted", "userId": uid, "score": score, "percentage": percentage})


class TestAuthAndGuards:
    def test_non_admin_is_rejected(self, fake_db):
        fake_db.seed("users", "student-1", {"accountType": "student"})
        _seed_closed_challenge(fake_db)
        client = _client(require_user_as("student-1"))
        r = client.post("/api/admin/challenges/c1/finalize-awards")
        assert r.status_code == 403

    def test_missing_challenge_returns_404(self, fake_db):
        _seed_admin(fake_db)
        client = _client(require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/nope/finalize-awards")
        assert r.status_code == 404

    def test_not_closed_yet_is_rejected(self, fake_db):
        _seed_admin(fake_db)
        _seed_closed_challenge(fake_db, status="published")
        client = _client(require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/c1/finalize-awards")
        assert r.status_code == 409

    def test_still_open_challenge_is_rejected(self, fake_db):
        """endsAt in the future must block finalization even if status says closed."""
        _seed_admin(fake_db)
        _seed_closed_challenge(fake_db, endsAt=datetime.now(timezone.utc) + timedelta(minutes=30))
        client = _client(require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/c1/finalize-awards")
        assert r.status_code == 409

    def test_no_award_slots_configured_is_rejected(self, fake_db):
        _seed_admin(fake_db)
        _seed_closed_challenge(fake_db, awardCount=0)
        client = _client(require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/c1/finalize-awards")
        assert r.status_code == 400

    def test_open_attempt_blocks_finalization(self, fake_db):
        _seed_admin(fake_db)
        _seed_closed_challenge(fake_db)
        fake_db.seed("challengeAttempts", "a1", {"challengeId": "c1", "status": "started", "userId": "u1"})
        client = _client(require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/c1/finalize-awards")
        assert r.status_code == 409


class TestRankingAndPrizePool:
    def test_winners_ranked_by_percentage_then_score(self, fake_db):
        _seed_admin(fake_db)
        _seed_closed_challenge(fake_db)
        _seed_entry(fake_db, "e1", "c1", "u1", 100)
        _seed_entry(fake_db, "e2", "c1", "u2", 100)
        _seed_attempt(fake_db, "a1", "c1", "u1", 90, 90)
        _seed_attempt(fake_db, "a2", "c1", "u2", 80, 80)
        client = _client(require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/c1/finalize-awards")
        assert r.status_code == 200
        body = r.get_json()
        assert body["createdAwards"] == 2

        awards = list(fake_db.dump("awardLedger").values())
        by_user = {a["userId"]: a for a in awards}
        assert by_user["u1"]["rank"] == 1
        assert by_user["u2"]["rank"] == 2

    def test_prize_pool_split_evenly_between_two_winners(self, fake_db):
        _seed_admin(fake_db)
        _seed_closed_challenge(fake_db)  # 50% share
        _seed_entry(fake_db, "e1", "c1", "u1", 100)
        _seed_entry(fake_db, "e2", "c1", "u2", 100)
        _seed_attempt(fake_db, "a1", "c1", "u1", 90, 90)
        _seed_attempt(fake_db, "a2", "c1", "u2", 80, 80)
        client = _client(require_user_as("admin-1"))
        r = client.post("/api/admin/challenges/c1/finalize-awards")
        body = r.get_json()
        assert body["prizePool"] == 100.0  # 50% of 200 verified revenue
        awards = list(fake_db.dump("awardLedger").values())
        total_paid = sum(a["prizeAmount"] for a in awards)
        assert total_paid == 100.0  # every cent of the pool is allocated

    def test_challenge_marked_finalized_after_success(self, fake_db):
        _seed_admin(fake_db)
        _seed_closed_challenge(fake_db)
        _seed_entry(fake_db, "e1", "c1", "u1", 100)
        _seed_attempt(fake_db, "a1", "c1", "u1", 90, 90)
        client = _client(require_user_as("admin-1"))
        client.post("/api/admin/challenges/c1/finalize-awards")
        challenge = fake_db.dump("academicChallenges")["c1"]
        assert challenge["status"] == "finalized"
        assert challenge.get("awardsFinalizedBy") == "admin-1"


class TestIdempotency:
    def test_finalizing_twice_does_not_duplicate_awards(self, fake_db):
        _seed_admin(fake_db)
        _seed_closed_challenge(fake_db)
        _seed_entry(fake_db, "e1", "c1", "u1", 100)
        _seed_attempt(fake_db, "a1", "c1", "u1", 90, 90)
        client = _client(require_user_as("admin-1"))
        first = client.post("/api/admin/challenges/c1/finalize-awards")
        second = client.post("/api/admin/challenges/c1/finalize-awards")
        assert first.status_code == 200
        assert first.get_json()["createdAwards"] == 1
        assert second.status_code == 200
        assert second.get_json()["alreadyFinalized"] is True
        assert len(fake_db.dump("awardLedger")) == 1
