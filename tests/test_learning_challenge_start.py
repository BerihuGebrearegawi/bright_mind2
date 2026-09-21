"""Real behavioural tests for learning_challenge_routes.py's
/api/challenges/start endpoint - by far the most complex/critical endpoint
in this file: role and grade gating, server-authoritative schedule
transitions (published->active, expired->closed), entry-fee verification,
the deterministic-attempt-id replay safety net (backed by a Firestore
transaction), question-set integrity checks, and round-advancement
qualification gating for multi-round seasonal challenges.
"""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from learning_challenge_routes import register_learning_challenge_routes
from tests.conftest import make_app


def require_user_as(uid):
    return lambda: (True, {"uid": uid})


def _client(fake_db, uid, firebase_configured=True):
    factory = (lambda: True) if firebase_configured else (lambda: None)
    app = make_app(register_learning_challenge_routes, require_user_as(uid), factory)
    return app.test_client()


def _seed_challenge(fake_db, cid="c1", **extra):
    doc = {"status": "published", "questionIds": ["q1"], "durationMinutes": 20}
    doc.update(extra)
    fake_db.seed("academicChallenges", cid, doc)
    fake_db.seed("questionBank", "q1", {
        "status": "approved", "question": "2+2?", "type": "mcq",
        "options": {"A": "3", "B": "4"}, "correctAnswer": "B", "points": 1,
    })


class TestRoleAndEligibilityGating:
    def test_non_student_is_rejected(self, fake_db):
        fake_db.seed("users", "u1", {"accountType": "teacher"})
        _seed_challenge(fake_db)
        client = _client(fake_db, "u1")
        r = client.post("/api/challenges/start", json={"challengeId": "c1"})
        assert r.status_code == 403

    def test_missing_challenge_returns_404(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/start", json={"challengeId": "nope"})
        assert r.status_code == 404

    def test_wrong_grade_is_rejected(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student", "className": "8"})
        _seed_challenge(fake_db, grade="9")
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/start", json={"challengeId": "c1"})
        assert r.status_code == 403


class TestServerAuthoritativeSchedule:
    def test_not_open_yet_is_rejected(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _seed_challenge(fake_db, startsAt=future)
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/start", json={"challengeId": "c1"})
        assert r.status_code == 403

    def test_expired_challenge_is_auto_closed_server_side(self, fake_db):
        """A client can never keep a challenge 'open' past its schedule -
        the server flips status to closed on the first request that notices."""
        fake_db.seed("users", "s1", {"accountType": "student"})
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _seed_challenge(fake_db, endsAt=past)
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/start", json={"challengeId": "c1"})
        assert r.status_code == 403
        assert fake_db.dump("academicChallenges")["c1"]["status"] == "closed"

    def test_published_challenge_becomes_active_on_first_start(self, fake_db):
        """A client-sent status can never activate a challenge - only the
        server does, in reaction to a real start request."""
        fake_db.seed("users", "s1", {"accountType": "student"})
        _seed_challenge(fake_db, status="published")
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/start", json={"challengeId": "c1"})
        assert r.status_code == 201
        assert fake_db.dump("academicChallenges")["c1"]["status"] == "active"


class TestEntryFeeVerification:
    def test_unpaid_entry_fee_blocks_start(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        _seed_challenge(fake_db, entryFee=50)
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/start", json={"challengeId": "c1"})
        assert r.status_code == 402

    def test_verified_entry_allows_start(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        _seed_challenge(fake_db, entryFee=50)
        fake_db.seed("challengeEntries", "e1", {"challengeId": "c1", "userId": "s1", "status": "verified"})
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/start", json={"challengeId": "c1"})
        assert r.status_code == 201


class TestReplaySafety:
    def test_restarting_an_in_progress_attempt_returns_the_same_attempt(self, fake_db):
        """The deterministic attempt id + transaction must prevent a second
        /start call from creating (or losing) a duplicate attempt."""
        fake_db.seed("users", "s1", {"accountType": "student"})
        _seed_challenge(fake_db)
        client = _client(fake_db, "s1")
        first = client.post("/api/challenges/start", json={"challengeId": "c1"})
        second = client.post("/api/challenges/start", json={"challengeId": "c1"})
        assert first.status_code == 201
        assert first.get_json()["attemptId"] == second.get_json()["attemptId"]
        assert len(fake_db.dump("challengeAttempts")) == 1

    def test_cannot_restart_an_already_submitted_attempt(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        _seed_challenge(fake_db)
        attempt_id = hashlib.sha256(b"s1:c1").hexdigest()[:40]
        fake_db.seed("challengeAttempts", attempt_id, {"challengeId": "c1", "userId": "s1", "status": "submitted"})
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/start", json={"challengeId": "c1"})
        assert r.status_code == 409


class TestQuestionSetIntegrity:
    def test_empty_question_set_is_rejected(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        fake_db.seed("academicChallenges", "c1", {"status": "published", "questionIds": []})
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/start", json={"challengeId": "c1"})
        assert r.status_code == 409

    def test_unapproved_question_blocks_start(self, fake_db):
        """A question that was un-approved/archived after the challenge was
        published must not be servable to a student."""
        fake_db.seed("users", "s1", {"accountType": "student"})
        fake_db.seed("academicChallenges", "c1", {"status": "published", "questionIds": ["q1"]})
        fake_db.seed("questionBank", "q1", {"status": "pending"})
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/start", json={"challengeId": "c1"})
        assert r.status_code == 409


class TestRoundAdvancementGating:
    def test_non_qualifier_cannot_start_the_next_round(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        _seed_challenge(fake_db, cid="round2", roundNumber=2, season="2026-A")
        fake_db.seed("academicChallenges", "round1", {"status": "closed", "season": "2026-A", "roundNumber": 1, "qualificationCount": 1})
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/start", json={"challengeId": "round2"})
        assert r.status_code == 403

    def test_qualifier_can_start_the_next_round(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        _seed_challenge(fake_db, cid="round2", roundNumber=2, season="2026-A")
        fake_db.seed("academicChallenges", "round1", {"status": "closed", "season": "2026-A", "roundNumber": 1, "qualificationCount": 1})
        fake_db.seed("challengeAttempts", "a1", {"challengeId": "round1", "userId": "s1", "status": "submitted", "percentage": 90, "score": 9})
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/start", json={"challengeId": "round2"})
        assert r.status_code == 201
