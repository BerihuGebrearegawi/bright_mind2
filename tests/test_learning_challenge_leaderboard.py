"""Real behavioural tests for learning_challenge_routes.py's
/api/challenges/<id>/leaderboard endpoint: the privacy boundary between
what a student sees (their own isMe flag, never another student's userId)
versus what a teacher/admin sees (full userId + numeric rank, plus a
qualified flag for round-advancement), and the entry-fee filter that
excludes unverified entrants from a paid challenge's leaderboard.
"""
import pytest

from learning_challenge_routes import register_learning_challenge_routes
from tests.conftest import make_app


def require_user_as(uid):
    return lambda: (True, {"uid": uid})


def _client(fake_db, uid, firebase_configured=True):
    factory = (lambda: True) if firebase_configured else (lambda: None)
    app = make_app(register_learning_challenge_routes, require_user_as(uid), factory)
    return app.test_client()


def _seed_two_attempts(fake_db, extra_challenge=None):
    fake_db.seed("academicChallenges", "c1", extra_challenge or {})
    fake_db.seed("challengeAttempts", "a1", {"challengeId": "c1", "status": "submitted", "userId": "s1", "percentage": 90, "score": 9})
    fake_db.seed("challengeAttempts", "a2", {"challengeId": "c1", "status": "submitted", "userId": "s2", "percentage": 80, "score": 8})


class TestAccessGating:
    def test_unapproved_teacher_is_rejected(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": False})
        _seed_two_attempts(fake_db)
        client = _client(fake_db, "t1")
        r = client.get("/api/challenges/c1/leaderboard")
        assert r.status_code == 403


class TestStudentViewPrivacy:
    def test_student_never_sees_another_students_userid(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        _seed_two_attempts(fake_db)
        client = _client(fake_db, "s1")
        r = client.get("/api/challenges/c1/leaderboard")
        assert r.status_code == 200
        rows = r.get_json()["leaderboard"]
        assert all("userId" not in row for row in rows)

    def test_student_gets_an_ismeflag_on_their_own_row(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        _seed_two_attempts(fake_db)
        client = _client(fake_db, "s1")
        rows = client.get("/api/challenges/c1/leaderboard").get_json()["leaderboard"]
        # s1 has the higher percentage, so ranks first.
        assert rows[0]["isMe"] is True
        assert rows[1]["isMe"] is False

    def test_student_view_has_no_rank_or_qualified_fields(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        _seed_two_attempts(fake_db, extra_challenge={"qualificationCount": 1})
        client = _client(fake_db, "s1")
        rows = client.get("/api/challenges/c1/leaderboard").get_json()["leaderboard"]
        assert all("rank" not in row and "qualified" not in row for row in rows)


class TestTeacherAdminView:
    def test_admin_sees_userid_and_numeric_rank(self, fake_db):
        fake_db.seed("users", "admin1", {"accountType": "admin"})
        _seed_two_attempts(fake_db)
        client = _client(fake_db, "admin1")
        rows = client.get("/api/challenges/c1/leaderboard").get_json()["leaderboard"]
        assert rows[0]["userId"] == "s1" and rows[0]["rank"] == 1
        assert rows[1]["userId"] == "s2" and rows[1]["rank"] == 2
        assert all("isMe" not in row for row in rows)

    def test_admin_sees_qualified_flag_when_challenge_has_a_qualification_count(self, fake_db):
        fake_db.seed("users", "admin1", {"accountType": "admin"})
        _seed_two_attempts(fake_db, extra_challenge={"qualificationCount": 1})
        client = _client(fake_db, "admin1")
        rows = client.get("/api/challenges/c1/leaderboard").get_json()["leaderboard"]
        assert rows[0]["qualified"] is True
        assert rows[1]["qualified"] is False


class TestEntryFeeFiltering:
    def test_unverified_entrant_is_excluded_from_a_paid_challenges_leaderboard(self, fake_db):
        fake_db.seed("users", "admin1", {"accountType": "admin"})
        _seed_two_attempts(fake_db, extra_challenge={"entryFee": 50})
        fake_db.seed("challengeEntries", "e1", {"challengeId": "c1", "userId": "s1", "status": "verified"})
        client = _client(fake_db, "admin1")
        rows = client.get("/api/challenges/c1/leaderboard").get_json()["leaderboard"]
        assert len(rows) == 1
        assert rows[0]["userId"] == "s1"
