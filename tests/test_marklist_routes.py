"""Behavioural tests for marklist_routes.py: the read-only Assignment/Quiz/
Exam aggregation for a teacher's grade and for a student's own record.
"""
from marklist_routes import register_marklist_routes
from tests.conftest import make_app


def require_user_as(uid, email="user@example.com"):
    return lambda: (True, {"uid": uid, "email": email})


def _client(require_user, firebase_configured=True):
    factory = (lambda: True) if firebase_configured else (lambda: None)
    app = make_app(register_marklist_routes, require_user, factory)
    return app.test_client()


class TestTeacherMarkList:
    def test_non_teacher_is_rejected(self, fake_db):
        client = _client(require_user_as("t1"))
        r = client.get("/api/teacher/marklist?className=7")
        assert r.status_code == 403

    def test_teacher_with_no_graded_work_in_grade_is_rejected(self, fake_db):
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(require_user_as("t1"))
        r = client.get("/api/teacher/marklist?className=7")
        assert r.status_code == 403

    def test_aggregates_assignment_and_exam_averages_per_student(self, fake_db):
        fake_db.seed("teachers", "t1", {"approved": True})
        fake_db.seed("users", "s1", {"role": "student", "className": "7", "displayName": "Abebe"})
        fake_db.seed("assignments", "a1", {"teacherUid": "t1", "className": "7"})
        fake_db.seed("exams", "e1", {"teacherUid": "t1", "className": "7"})
        fake_db.seed("assignmentSubmissions", "sub1", {
            "assignmentId": "a1", "studentUid": "s1", "status": "graded", "percentage": 80,
        })
        fake_db.seed("examAttempts", "att1", {
            "examId": "e1", "userId": "s1", "status": "submitted", "percentage": 60,
        })
        client = _client(require_user_as("t1"))
        r = client.get("/api/teacher/marklist?className=7")
        assert r.status_code == 200
        row = r.get_json()["students"][0]
        assert row["assignmentAverage"] == 80
        assert row["examAverage"] == 60
        assert row["overallAverage"] == 70.0

    def test_ungraded_submission_is_excluded(self, fake_db):
        fake_db.seed("teachers", "t1", {"approved": True})
        fake_db.seed("users", "s1", {"role": "student", "className": "7", "displayName": "Abebe"})
        fake_db.seed("assignments", "a1", {"teacherUid": "t1", "className": "7"})
        fake_db.seed("assignmentSubmissions", "sub1", {
            "assignmentId": "a1", "studentUid": "s1", "status": "submitted", "percentage": None,
        })
        client = _client(require_user_as("t1"))
        r = client.get("/api/teacher/marklist?className=7")
        assert r.status_code == 200
        assert r.get_json()["students"] == []

    def test_another_teachers_assignment_is_not_counted(self, fake_db):
        fake_db.seed("teachers", "t1", {"approved": True})
        fake_db.seed("teachers", "t2", {"approved": True})
        fake_db.seed("users", "s1", {"role": "student", "className": "7", "displayName": "Abebe"})
        fake_db.seed("assignments", "a1", {"teacherUid": "t1", "className": "7"})
        fake_db.seed("assignments", "a2", {"teacherUid": "t2", "className": "7"})
        fake_db.seed("assignmentSubmissions", "sub1", {
            "assignmentId": "a1", "studentUid": "s1", "status": "graded", "percentage": 90,
        })
        fake_db.seed("assignmentSubmissions", "sub2", {
            "assignmentId": "a2", "studentUid": "s1", "status": "graded", "percentage": 10,
        })
        client = _client(require_user_as("t1"))
        r = client.get("/api/teacher/marklist?className=7")
        row = r.get_json()["students"][0]
        assert row["assignmentAverage"] == 90
        assert row["assignmentCount"] == 1


class TestStudentMarkList:
    def test_combines_assignment_quiz_and_exam(self, fake_db):
        fake_db.seed("assignments", "a1", {"title": "HW1"})
        fake_db.seed("assignmentSubmissions", "sub1", {
            "assignmentId": "a1", "studentUid": "s1", "status": "graded", "score": 8, "maxScore": 10, "percentage": 80,
        })
        fake_db.seed("exams", "e1", {"title": "Midterm"})
        fake_db.seed("examAttempts", "att1", {
            "examId": "e1", "userId": "s1", "status": "submitted", "score": 6, "totalPoints": 10, "percentage": 60,
        })
        fake_db.seed("quizzes", "q1", {"title": "Pop Quiz"})
        fake_db.seed("quizResults", "qr1", {
            "quizId": "q1", "studentUid": "s1", "score": 1, "total": 1, "percentage": 100,
        })
        client = _client(require_user_as("s1"))
        r = client.get("/api/student/marklist")
        assert r.status_code == 200
        body = r.get_json()
        assert body["assignments"][0]["title"] == "HW1"
        assert body["exams"][0]["title"] == "Midterm"
        assert body["quizzes"][0]["title"] == "Pop Quiz"
        s = body["summary"]
        assert s["assignmentAverage"] == 80
        assert s["examAverage"] == 60
        assert s["quizAverage"] == 100
        assert s["overallAverage"] == 80.0

    def test_only_sees_own_records(self, fake_db):
        fake_db.seed("assignments", "a1", {"title": "HW1"})
        fake_db.seed("assignmentSubmissions", "sub1", {
            "assignmentId": "a1", "studentUid": "other", "status": "graded", "percentage": 50,
        })
        client = _client(require_user_as("s1"))
        r = client.get("/api/student/marklist")
        assert r.get_json()["assignments"] == []

    def test_no_graded_work_returns_empty_summary(self, fake_db):
        client = _client(require_user_as("s1"))
        r = client.get("/api/student/marklist")
        assert r.status_code == 200
        s = r.get_json()["summary"]
        assert s["overallAverage"] is None


def require_user_denied(status=401):
    return lambda: (False, ({"error": "Sign in required."}, status))


class TestSecurity:
    def test_unauthenticated_request_is_rejected_on_every_endpoint(self, fake_db):
        client = _client(require_user_denied())
        assert client.get("/api/teacher/marklist?className=7").status_code == 401
        assert client.get("/api/student/marklist").status_code == 401
