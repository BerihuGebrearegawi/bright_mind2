"""Real behavioural tests for learning_challenge_routes.py's chapter-practice
endpoints (/api/practice/start and /api/practice/submit): candidate
filtering (approved status, matching book/chapter/subchapter), session
ownership, the already-submitted guard, and scoring - which, unlike the
competitive challenge submit endpoint, deliberately does return each
question's correctAnswer as practice feedback.
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


class TestPracticeStart:
    def test_non_student_is_rejected(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        client = _client(fake_db, "t1")
        r = client.post("/api/practice/start", json={"bookId": "b1", "chapterId": "ch1"})
        assert r.status_code == 403

    def test_missing_book_or_chapter_is_rejected(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        client = _client(fake_db, "s1")
        r = client.post("/api/practice/start", json={})
        assert r.status_code == 400

    def test_no_matching_questions_returns_404(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        client = _client(fake_db, "s1")
        r = client.post("/api/practice/start", json={"bookId": "b1", "chapterId": "ch1"})
        assert r.status_code == 404

    def test_candidates_are_filtered_to_approved_matching_chapter_and_book(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        fake_db.seed("questionBank", "q1", {"status": "approved", "chapterId": "ch1", "bookId": "b1"})
        fake_db.seed("questionBank", "q2", {"status": "draft", "chapterId": "ch1", "bookId": "b1"})  # not approved
        fake_db.seed("questionBank", "q3", {"status": "approved", "chapterId": "ch1", "bookId": "OTHER"})  # wrong book
        client = _client(fake_db, "s1")
        r = client.post("/api/practice/start", json={"bookId": "b1", "chapterId": "ch1"})
        assert r.status_code == 201
        body = r.get_json()
        assert body["questionCount"] == 1
        sessions = fake_db.dump("practiceSessions")
        assert len(sessions) == 1
        assert sessions[body["sessionId"]]["userId"] == "s1"


class TestPracticeSubmit:
    def test_missing_session_returns_404(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        client = _client(fake_db, "s1")
        r = client.post("/api/practice/submit", json={"sessionId": "nope", "answers": {}})
        assert r.status_code == 404

    def test_submitting_someone_elses_session_is_forbidden(self, fake_db):
        fake_db.seed("users", "s2", {"accountType": "student"})
        fake_db.seed("practiceSessions", "sess1", {"userId": "s1", "status": "started", "questionIds": ["q1"]})
        client = _client(fake_db, "s2")
        r = client.post("/api/practice/submit", json={"sessionId": "sess1", "answers": {}})
        assert r.status_code == 403

    def test_already_submitted_session_is_rejected(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        fake_db.seed("practiceSessions", "sess1", {"userId": "s1", "status": "submitted", "questionIds": ["q1"]})
        client = _client(fake_db, "s1")
        r = client.post("/api/practice/submit", json={"sessionId": "sess1", "answers": {}})
        assert r.status_code == 409

    def test_score_computed_and_correct_answer_returned_as_feedback(self, fake_db):
        """Practice is meant to teach, so - unlike a competitive challenge
        submission - the response legitimately includes each correctAnswer."""
        fake_db.seed("users", "s1", {"accountType": "student"})
        fake_db.seed("practiceSessions", "sess1", {"userId": "s1", "status": "started", "questionIds": ["q1", "q2"]})
        fake_db.seed("questionBank", "q1", {"correctAnswer": "B", "points": 1})
        fake_db.seed("questionBank", "q2", {"correctAnswer": "A", "points": 1})
        client = _client(fake_db, "s1")
        r = client.post("/api/practice/submit", json={"sessionId": "sess1", "answers": {"q1": "B", "q2": "X"}})
        assert r.status_code == 200
        body = r.get_json()
        assert body["score"] == 1.0
        assert body["percentage"] == 50.0
        assert any(item["correctAnswer"] == "B" for item in body["results"])
        assert fake_db.dump("practiceSessions")["sess1"]["status"] == "submitted"


class TestAssignQuestionsToClass:
    def test_non_teacher_is_rejected(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        client = _client(fake_db, "s1")
        r = client.post("/api/question-bank/assign", json={"questionIds": ["q1"], "className": "9"})
        assert r.status_code == 403

    def test_invalid_class_name_is_rejected(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(fake_db, "t1")
        r = client.post("/api/question-bank/assign", json={"questionIds": ["q1"], "className": "99"})
        assert r.status_code == 400

    def test_empty_question_list_is_rejected(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(fake_db, "t1")
        r = client.post("/api/question-bank/assign", json={"questionIds": [], "className": "9"})
        assert r.status_code == 400

    def test_all_unapproved_questions_is_rejected(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        fake_db.seed("questionBank", "q1", {"status": "draft"})
        client = _client(fake_db, "t1")
        r = client.post("/api/question-bank/assign", json={"questionIds": ["q1"], "className": "9"})
        assert r.status_code == 409

    def test_mixed_valid_and_missing_ids_assigns_valid_and_reports_skipped(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        fake_db.seed("questionBank", "q1", {"status": "approved", "question": "Q1", "correctAnswer": "A", "options": {"A": "x"}})
        client = _client(fake_db, "t1")
        r = client.post("/api/question-bank/assign", json={
            "questionIds": ["q1", "missing"], "className": "9", "assignmentType": "groupwork",
        })
        assert r.status_code == 201
        body = r.get_json()
        assert body["assignedCount"] == 1
        assert body["skipped"] == ["missing"]
        quizzes = fake_db.dump("quizzes")
        assert list(quizzes.values())[0]["className"] == "9"
        assert list(quizzes.values())[0]["assignmentType"] == "groupwork"
