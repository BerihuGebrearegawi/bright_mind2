"""Real behavioural tests for learning_challenge_routes.py's
/api/challenges/submit endpoint: ownership checks, server-side grading from
the immutable question snapshot (never from client-supplied correctness),
idempotent resubmission, and - the most security-sensitive rule here - once
an attempt's deadline has passed, only answers that were durably saved
*before* expiry are graded, so a client can't slip in new answers via a
late /submit call with a different answers payload.
"""
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


def _snapshot_q(qid, points, correct):
    return {"id": qid, "correctAnswer": correct, "points": points}


class TestOwnershipAndExistence:
    def test_missing_attempt_returns_404(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/submit", json={"challengeId": "c1", "attemptId": "nope", "answers": {}})
        assert r.status_code == 404

    def test_submitting_someone_elses_attempt_is_forbidden(self, fake_db):
        fake_db.seed("users", "s2", {"accountType": "student"})
        fake_db.seed("challengeAttempts", "a1", {"challengeId": "c1", "userId": "s1", "status": "started"})
        client = _client(fake_db, "s2")
        r = client.post("/api/challenges/submit", json={"challengeId": "c1", "attemptId": "a1", "answers": {}})
        assert r.status_code == 403


class TestServerSideGrading:
    def test_score_and_percentage_computed_from_snapshot(self, fake_db):
        now = datetime.now(timezone.utc)
        fake_db.seed("users", "s1", {"accountType": "student"})
        fake_db.seed("academicChallenges", "c1", {"status": "active", "endsAt": (now + timedelta(minutes=30)).isoformat()})
        fake_db.seed("challengeAttempts", "a1", {
            "challengeId": "c1", "userId": "s1", "status": "started",
            "questionIds": ["q1", "q2"],
            "questionSnapshot": [_snapshot_q("q1", 1, "B"), _snapshot_q("q2", 1, "A")],
            "deadlineAt": now + timedelta(minutes=30),
        })
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/submit", json={"challengeId": "c1", "attemptId": "a1", "answers": {"q1": "B", "q2": "X"}})
        assert r.status_code == 200
        body = r.get_json()
        assert body["score"] == 1.0
        assert body["percentage"] == 50.0
        assert body["correctCount"] == 1

    def test_resubmitting_is_idempotent_and_does_not_regrade(self, fake_db):
        now = datetime.now(timezone.utc)
        fake_db.seed("users", "s1", {"accountType": "student"})
        fake_db.seed("academicChallenges", "c1", {"status": "active", "endsAt": (now + timedelta(minutes=30)).isoformat()})
        fake_db.seed("challengeAttempts", "a1", {
            "challengeId": "c1", "userId": "s1", "status": "started",
            "questionIds": ["q1"], "questionSnapshot": [_snapshot_q("q1", 1, "B")],
            "deadlineAt": now + timedelta(minutes=30),
        })
        client = _client(fake_db, "s1")
        client.post("/api/challenges/submit", json={"challengeId": "c1", "attemptId": "a1", "answers": {"q1": "B"}})
        second = client.post("/api/challenges/submit", json={"challengeId": "c1", "attemptId": "a1", "answers": {"q1": "WRONG"}})
        assert second.status_code == 200
        assert second.get_json()["alreadySubmitted"] is True
        assert second.get_json()["score"] == 1.0  # not re-graded with the second call's answer


class TestExpiredAttemptAnswerSource:
    def test_expired_attempt_grades_only_durably_saved_answers(self, fake_db):
        """The critical security property: once the deadline has passed, the
        answers payload on this very request is ignored, and grading uses
        only what was saved to Firestore before expiry."""
        now = datetime.now(timezone.utc)
        fake_db.seed("users", "s1", {"accountType": "student"})
        fake_db.seed("academicChallenges", "c1", {"status": "active", "endsAt": (now + timedelta(minutes=30)).isoformat()})
        fake_db.seed("challengeAttempts", "a1", {
            "challengeId": "c1", "userId": "s1", "status": "started",
            "questionIds": ["q1"], "questionSnapshot": [_snapshot_q("q1", 1, "B")],
            "deadlineAt": now - timedelta(minutes=5),  # already expired
            "answers": {"q1": "B"},  # durably saved, correct
        })
        client = _client(fake_db, "s1")
        # Attacker/late client tries to submit a different answer after the deadline.
        r = client.post("/api/challenges/submit", json={"challengeId": "c1", "attemptId": "a1", "answers": {"q1": "A"}})
        assert r.status_code == 200
        body = r.get_json()
        assert body["score"] == 1.0  # graded from the saved "B", not the late "A"
        assert body["expiredSubmission"] is True

    def test_manually_closed_non_expired_challenge_rejects_submission(self, fake_db):
        """A challenge closed by an admin (not by its own schedule) must not
        accept a late finalize even for an attempt that was already started."""
        now = datetime.now(timezone.utc)
        fake_db.seed("users", "s1", {"accountType": "student"})
        fake_db.seed("academicChallenges", "c1", {"status": "closed"})  # no endsAt -> not schedule-expired
        fake_db.seed("challengeAttempts", "a1", {
            "challengeId": "c1", "userId": "s1", "status": "started",
            "questionIds": ["q1"], "questionSnapshot": [_snapshot_q("q1", 1, "B")],
            "deadlineAt": now + timedelta(minutes=30),
        })
        client = _client(fake_db, "s1")
        r = client.post("/api/challenges/submit", json={"challengeId": "c1", "attemptId": "a1", "answers": {"q1": "B"}})
        assert r.status_code == 409
