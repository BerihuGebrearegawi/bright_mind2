"""Real behavioural tests for teacher_routes.py's assignment submit/grade
flow: submission eligibility guards (grade match, due date, already-graded
lock, submit-then-resubmit), and teacher grading (ownership check, score
bounds, percentage calculation).
"""
from datetime import datetime, timedelta, timezone

import pytest

from teacher_routes import register_teacher_routes
from tests.conftest import make_app


def require_user_as(uid, email="student@example.com"):
    return lambda: (True, {"uid": uid, "email": email})


def _client(require_user, firebase_configured=True):
    factory = (lambda: True) if firebase_configured else (lambda: None)
    app = make_app(register_teacher_routes, require_user, lambda: (True, {}), factory)
    return app.test_client()


class TestStudentSubmitAssignment:
    def test_empty_submission_is_rejected(self, fake_db):
        client = _client(require_user_as("s1"))
        r = client.post("/api/student/assignments/a1/submit", json={})
        assert r.status_code == 400

    def test_missing_assignment_returns_404(self, fake_db):
        client = _client(require_user_as("s1"))
        r = client.post("/api/student/assignments/nope/submit", json={"text": "hi"})
        assert r.status_code == 404

    def test_unpublished_assignment_rejects_submission(self, fake_db):
        fake_db.seed("assignments", "a1", {"status": "draft"})
        client = _client(require_user_as("s1"))
        r = client.post("/api/student/assignments/a1/submit", json={"text": "hi"})
        assert r.status_code == 409

    def test_wrong_grade_is_rejected(self, fake_db):
        fake_db.seed("assignments", "a1", {"status": "published", "className": "Grade 9"})
        fake_db.seed("users", "s1", {"className": "Grade 8"})
        client = _client(require_user_as("s1"))
        r = client.post("/api/student/assignments/a1/submit", json={"text": "hi"})
        assert r.status_code == 403

    def test_past_due_date_is_rejected(self, fake_db):
        past_due = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        fake_db.seed("assignments", "a1", {"status": "published", "dueAt": past_due})
        client = _client(require_user_as("s1"))
        r = client.post("/api/student/assignments/a1/submit", json={"text": "hi"})
        assert r.status_code == 409

    def test_graded_submission_cannot_be_resubmitted(self, fake_db):
        fake_db.seed("assignments", "a1", {"status": "published"})
        fake_db.seed("assignmentSubmissions", "sub1", {"assignmentId": "a1", "studentUid": "s1", "status": "graded"})
        client = _client(require_user_as("s1"))
        r = client.post("/api/student/assignments/a1/submit", json={"text": "hi"})
        assert r.status_code == 409

    def test_first_submission_creates_a_new_document(self, fake_db):
        fake_db.seed("assignments", "a1", {"status": "published"})
        client = _client(require_user_as("s1"))
        r = client.post("/api/student/assignments/a1/submit", json={"text": "my answer"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is True
        subs = fake_db.dump("assignmentSubmissions")
        assert len(subs) == 1
        assert subs[body["submissionId"]]["text"] == "my answer"
        assert subs[body["submissionId"]]["status"] == "submitted"

    def test_resubmitting_before_grading_updates_the_same_document(self, fake_db):
        fake_db.seed("assignments", "a1", {"status": "published"})
        fake_db.seed("assignmentSubmissions", "sub1", {"assignmentId": "a1", "studentUid": "s1", "status": "submitted", "text": "old"})
        client = _client(require_user_as("s1"))
        r = client.post("/api/student/assignments/a1/submit", json={"text": "updated answer"})
        assert r.status_code == 200
        assert r.get_json()["submissionId"] == "sub1"
        subs = fake_db.dump("assignmentSubmissions")
        assert len(subs) == 1  # no duplicate created
        assert subs["sub1"]["text"] == "updated answer"


class TestTeacherGradeSubmission:
    def test_non_teacher_is_rejected(self, fake_db):
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/submissions/sub1/grade", json={"score": 8, "maxScore": 10})
        assert r.status_code == 403

    def test_non_numeric_score_is_rejected(self, fake_db):
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/submissions/sub1/grade", json={"score": "not-a-number"})
        assert r.status_code == 400

    def test_score_above_max_is_rejected(self, fake_db):
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/submissions/sub1/grade", json={"score": 15, "maxScore": 10})
        assert r.status_code == 400

    def test_missing_submission_returns_404(self, fake_db):
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/submissions/nope/grade", json={"score": 8, "maxScore": 10})
        assert r.status_code == 404

    def test_grading_someone_elses_assignment_is_forbidden(self, fake_db):
        fake_db.seed("teachers", "t1", {"approved": True})
        fake_db.seed("assignmentSubmissions", "sub1", {"assignmentId": "a1"})
        fake_db.seed("assignments", "a1", {"teacherUid": "other-teacher"})
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/submissions/sub1/grade", json={"score": 8, "maxScore": 10})
        assert r.status_code == 403

    def test_successful_grading_computes_percentage_and_saves_feedback(self, fake_db):
        fake_db.seed("teachers", "t1", {"approved": True})
        fake_db.seed("assignmentSubmissions", "sub1", {"assignmentId": "a1"})
        fake_db.seed("assignments", "a1", {"teacherUid": "t1"})
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/submissions/sub1/grade", json={"score": 8, "maxScore": 10, "feedback": "Good job"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["percentage"] == 80.0
        sub = fake_db.dump("assignmentSubmissions")["sub1"]
        assert sub["status"] == "graded"
        assert sub["gradedBy"] == "t1"
        assert sub["feedback"] == "Good job"
