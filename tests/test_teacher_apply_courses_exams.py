"""Real behavioural tests for the remaining teacher_routes.py endpoints:
teacher application (validation + duplicate-application guards), course and
lesson creation (ownership checks), and exam creation/publishing - including
the security-relevant separation of correct answers (examKeys) from the
public exam document.
"""
import pytest

from teacher_routes import register_teacher_routes
from tests.conftest import make_app


def require_user_as(uid, email="teacher@example.com"):
    return lambda: (True, {"uid": uid, "email": email})


def _client(require_user, firebase_configured=True):
    factory = (lambda: True) if firebase_configured else (lambda: None)
    app = make_app(register_teacher_routes, require_user, lambda: (True, {}), factory)
    return app.test_client()


def _teacher_client(fake_db, uid="t1"):
    fake_db.seed("teachers", uid, {"approved": True})
    return _client(require_user_as(uid))


class TestTeacherApply:
    def test_missing_name_and_education_is_rejected(self, fake_db):
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/apply", json={"name": "", "educationLevel": ""})
        assert r.status_code == 400

    def test_unsupported_subject_is_rejected(self, fake_db):
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/apply", json={
            "name": "Abebe", "educationLevel": "BA", "subjects": ["Klingon"], "classes": ["9"],
        })
        assert r.status_code == 400

    def test_out_of_range_grade_is_rejected(self, fake_db):
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/apply", json={
            "name": "Abebe", "educationLevel": "BA", "subjects": ["Mathematics"], "classes": ["99"],
        })
        assert r.status_code == 400

    def test_already_approved_teacher_cannot_reapply(self, fake_db):
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/apply", json={
            "name": "Abebe", "educationLevel": "BA", "subjects": ["Mathematics"], "classes": ["9"],
        })
        assert r.status_code == 409

    def test_duplicate_pending_application_is_rejected(self, fake_db):
        fake_db.seed("teacherRequests", "req1", {"uid": "t1", "status": "pending"})
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/apply", json={
            "name": "Abebe", "educationLevel": "BA", "subjects": ["Mathematics"], "classes": ["9"],
        })
        assert r.status_code == 409

    def test_successful_application_creates_a_pending_request(self, fake_db):
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/apply", json={
            "name": "Abebe", "educationLevel": "BA", "subjects": ["Mathematics", "Physics"],
            "classes": ["9", "10"], "experienceYears": "5",
        })
        assert r.status_code == 201
        body = r.get_json()
        assert "requestId" in body
        requests = fake_db.dump("teacherRequests")
        assert len(requests) == 1
        assert requests[body["requestId"]]["status"] == "pending"


class TestCourseAndLessonOwnership:
    def test_course_requires_title_and_valid_grade(self, fake_db):
        client = _teacher_client(fake_db)
        r = client.post("/api/teacher/courses", json={"title": "", "className": "9"})
        assert r.status_code == 400

    def test_create_course_succeeds(self, fake_db):
        client = _teacher_client(fake_db)
        r = client.post("/api/teacher/courses", json={"title": "Algebra", "className": "9"})
        assert r.status_code == 201
        assert r.get_json()["course"]["title"] == "Algebra"

    def test_lesson_on_someone_elses_course_is_forbidden(self, fake_db):
        fake_db.seed("teachers", "t2", {"approved": True})
        fake_db.seed("courses", "c-other", {"teacherUid": "t1"})
        client = _client(require_user_as("t2"))
        r = client.post("/api/teacher/lessons", json={"courseId": "c-other", "title": "L1", "url": "http://x"})
        assert r.status_code == 403

    def test_create_lesson_on_own_course_succeeds(self, fake_db):
        fake_db.seed("courses", "c1", {"teacherUid": "t1"})
        client = _teacher_client(fake_db)
        r = client.post("/api/teacher/lessons", json={
            "courseId": "c1", "title": "Intro", "url": "http://video", "contentType": "youtube",
        })
        assert r.status_code == 201


class TestExamCreationAndPublishing:
    def test_true_false_answer_is_converted_and_key_kept_separate(self, fake_db):
        """The public exam document must never contain the correct answer;
        it is stored only in the separate examKeys collection."""
        client = _teacher_client(fake_db)
        r = client.post("/api/teacher/exams", json={
            "title": "Quiz1",
            "questions": [
                {"question": "Is the sky blue?", "type": "true_false", "answer": "True"},
                {"question": "2+2?", "type": "mcq", "answer": "B", "options": {"A": "3", "B": "4"}},
            ],
        })
        assert r.status_code == 201
        exam_id = r.get_json()["examId"]
        exam = fake_db.dump("exams")[exam_id]
        assert all("answer" not in q for q in exam["questions"])
        assert exam["status"] == "draft"
        key = fake_db.dump("examKeys")[exam_id]
        assert key["answers"]["0"] == "A"  # True -> option A
        assert key["answers"]["1"] == "B"

    def test_publish_succeeds_when_answer_key_exists(self, fake_db):
        client = _teacher_client(fake_db)
        r = client.post("/api/teacher/exams", json={
            "title": "Quiz1",
            "questions": [{"question": "2+2?", "type": "mcq", "answer": "B", "options": {"A": "3", "B": "4"}}],
        })
        exam_id = r.get_json()["examId"]
        pub = client.post(f"/api/teacher/exams/{exam_id}/publish")
        assert pub.status_code == 200
        assert fake_db.dump("exams")[exam_id]["status"] == "published"

    def test_publish_without_answer_key_is_rejected(self, fake_db):
        """An exam document that somehow exists without its examKeys sibling
        (e.g. partial write) must not be publishable."""
        fake_db.seed("exams", "e2", {"teacherUid": "t1", "status": "draft"})
        client = _teacher_client(fake_db)
        r = client.post("/api/teacher/exams/e2/publish")
        assert r.status_code == 409

    def test_publish_someone_elses_exam_is_forbidden(self, fake_db):
        fake_db.seed("exams", "e3", {"teacherUid": "other-teacher"})
        client = _teacher_client(fake_db)
        r = client.post("/api/teacher/exams/e3/publish")
        assert r.status_code == 403
