"""Real behavioural tests for app.py's server-authoritative exam flow.

The interesting guarantees here: the answer key (examKeys) never reaches
the browser before grading, grading is transactional and idempotent
(re-grading an already-submitted attempt returns the stored result rather
than re-scoring), and a late/expired submission is graded off the
server's own autosaved answers rather than trusting a late browser
payload.
"""
from datetime import datetime, timedelta, timezone

import pytest


def require_user_as(app_module, monkeypatch, uid="student-1", **extra):
    detail = {"uid": uid, **extra}
    monkeypatch.setattr(app_module, "_require_user_bearer", lambda: (True, detail))
    return detail


def _client(app_module):
    return app_module.app.test_client()


def _seed_student(fake_db, uid, class_name="7"):
    fake_db.seed("users", uid, {"accountType": "student", "className": class_name})


def _seed_exam(fake_db, exam_id, class_name="7", status="published", **extra):
    doc = {
        "title": "Midterm", "className": class_name, "status": status,
        "durationMinutes": 30, "passMark": 50, "maxAttempts": 1,
        "questions": [
            {"question": "1+1?", "type": "mcq", "options": {"A": "1", "B": "2"}, "points": 1, "topic": "Arithmetic"},
            {"question": "2+2?", "type": "mcq", "options": {"A": "3", "B": "4"}, "points": 1, "topic": "Arithmetic"},
        ],
    }
    doc.update(extra)
    fake_db.seed("exams", exam_id, doc)


def _seed_key(fake_db, exam_id, answers=None, points=None, topics=None):
    fake_db.seed("examKeys", exam_id, {
        "answers": answers or {"0": "B", "1": "B"},
        "points": points or {"0": 1, "1": 1},
        "topics": topics or {"0": "Arithmetic", "1": "Arithmetic"},
    })


class TestAvailableExams:
    def test_non_student_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="teacher-1")
        fake_db.seed("users", "teacher-1", {"accountType": "teacher"})
        r = _client(app_module).get("/api/exams/available")
        assert r.status_code == 403

    def test_no_class_configured_returns_empty_with_warning(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        fake_db.seed("users", "student-1", {"accountType": "student"})
        r = _client(app_module).get("/api/exams/available")
        assert r.status_code == 200
        assert r.get_json()["exams"] == []

    def test_only_published_matching_class_exams_are_listed(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        _seed_student(fake_db, "student-1", "7")
        _seed_exam(fake_db, "exam-match", class_name="7", status="published")
        _seed_exam(fake_db, "exam-other-class", class_name="8", status="published")
        _seed_exam(fake_db, "exam-draft", class_name="7", status="draft")
        r = _client(app_module).get("/api/exams/available")
        assert r.status_code == 200
        ids = [e["id"] for e in r.get_json()["exams"]]
        assert ids == ["exam-match"]

    def test_answer_key_is_never_included(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        _seed_student(fake_db, "student-1", "7")
        _seed_exam(fake_db, "exam-match", class_name="7")
        r = _client(app_module).get("/api/exams/available")
        body = str(r.get_json())
        assert "correctAnswer" not in body and "answers" not in body


class TestStartExam:
    def test_unpublished_exam_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        _seed_student(fake_db, "student-1")
        _seed_exam(fake_db, "exam1", status="draft")
        r = _client(app_module).post("/api/exams/start", json={"examId": "exam1"})
        assert r.status_code == 403

    def test_wrong_class_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        _seed_student(fake_db, "student-1", "7")
        _seed_exam(fake_db, "exam1", class_name="8")
        r = _client(app_module).post("/api/exams/start", json={"examId": "exam1"})
        assert r.status_code == 403

    def test_questions_never_include_correct_answers(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        _seed_student(fake_db, "student-1", "7")
        _seed_exam(fake_db, "exam1", class_name="7")
        r = _client(app_module).post("/api/exams/start", json={"examId": "exam1"})
        assert r.status_code == 201
        body = str(r.get_json())
        assert "correctAnswer" not in body

    def test_max_attempts_reached_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        _seed_student(fake_db, "student-1", "7")
        _seed_exam(fake_db, "exam1", class_name="7", maxAttempts=1)
        fake_db.seed("examAttempts", "prior", {"examId": "exam1", "userId": "student-1", "status": "submitted"})
        r = _client(app_module).post("/api/exams/start", json={"examId": "exam1"})
        assert r.status_code == 409

    def test_resuming_existing_active_attempt_returns_same_id(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        _seed_student(fake_db, "student-1", "7")
        _seed_exam(fake_db, "exam1", class_name="7")
        now = datetime.now(timezone.utc)
        fake_db.seed("examAttempts", "active1", {"examId": "exam1", "userId": "student-1", "status": "started",
                                                   "startedAt": now, "deadlineAt": now + timedelta(minutes=30)})
        r = _client(app_module).post("/api/exams/start", json={"examId": "exam1"})
        assert r.status_code == 200
        assert r.get_json()["attemptId"] == "active1"

    def test_happy_path_creates_attempt(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        _seed_student(fake_db, "student-1", "7")
        _seed_exam(fake_db, "exam1", class_name="7", durationMinutes=45)
        r = _client(app_module).post("/api/exams/start", json={"examId": "exam1"})
        assert r.status_code == 201
        body = r.get_json()
        attempt = fake_db.dump("examAttempts")[body["attemptId"]]
        assert attempt["status"] == "started"
        assert attempt["userId"] == "student-1"


class TestSaveExamAnswers:
    def test_wrong_owner_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-2")
        fake_db.seed("examAttempts", "att1", {"userId": "student-1", "examId": "exam1", "status": "started"})
        r = _client(app_module).post("/api/exams/save", json={"examId": "exam1", "attemptId": "att1", "answers": {"0": "B"}})
        assert r.status_code == 403

    def test_already_submitted_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        fake_db.seed("examAttempts", "att1", {"userId": "student-1", "examId": "exam1", "status": "submitted"})
        r = _client(app_module).post("/api/exams/save", json={"examId": "exam1", "attemptId": "att1", "answers": {"0": "B"}})
        assert r.status_code == 409

    def test_expired_attempt_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        fake_db.seed("examAttempts", "att1", {"userId": "student-1", "examId": "exam1", "status": "started", "deadlineAt": past})
        r = _client(app_module).post("/api/exams/save", json={"examId": "exam1", "attemptId": "att1", "answers": {"0": "B"}})
        assert r.status_code == 409
        assert r.get_json()["expired"] is True

    def test_happy_path_saves_answers(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        future = datetime.now(timezone.utc) + timedelta(minutes=20)
        fake_db.seed("examAttempts", "att1", {"userId": "student-1", "examId": "exam1", "status": "started", "deadlineAt": future})
        r = _client(app_module).post("/api/exams/save", json={"examId": "exam1", "attemptId": "att1", "answers": {"0": "B", "1": "A"}})
        assert r.status_code == 200
        assert fake_db.dump("examAttempts")["att1"]["answers"] == {"0": "B", "1": "A"}


class TestGradeExam:
    def _seed_started(self, fake_db, attempt_id="att1", uid="student-1", exam_id="exam1", deadline_delta_minutes=20, pass_mark=50):
        deadline = datetime.now(timezone.utc) + timedelta(minutes=deadline_delta_minutes)
        fake_db.seed("examAttempts", attempt_id, {"userId": uid, "examId": exam_id, "status": "started",
                                                    "deadlineAt": deadline, "passMark": pass_mark, "answers": {}})

    def test_wrong_owner_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-2")
        self._seed_started(fake_db, uid="student-1")
        r = _client(app_module).post("/api/exams/grade", json={"examId": "exam1", "attemptId": "att1", "answers": {}})
        assert r.status_code == 403

    def test_attempt_not_open_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        fake_db.seed("examAttempts", "att1", {"userId": "student-1", "examId": "exam1", "status": "expired"})
        r = _client(app_module).post("/api/exams/grade", json={"examId": "exam1", "attemptId": "att1", "answers": {}})
        assert r.status_code == 409

    def test_grading_data_unavailable_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        self._seed_started(fake_db)
        # No exam / examKeys doc seeded.
        r = _client(app_module).post("/api/exams/grade", json={"examId": "exam1", "attemptId": "att1", "answers": {}})
        assert r.status_code == 404

    def test_correct_scoring_and_pass_status(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        self._seed_started(fake_db)
        _seed_exam(fake_db, "exam1")
        _seed_key(fake_db, "exam1")
        r = _client(app_module).post("/api/exams/grade",
                                      json={"examId": "exam1", "attemptId": "att1", "answers": {"0": "B", "1": "B"}})
        assert r.status_code == 200
        body = r.get_json()
        assert body["score"] == 2
        assert body["totalPoints"] == 2
        assert body["percentage"] == 100.0
        assert body["status"] == "PASS"
        attempt = fake_db.dump("examAttempts")["att1"]
        assert attempt["status"] == "submitted"

    def test_partial_score_and_fail_status(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        self._seed_started(fake_db, pass_mark=80)
        _seed_exam(fake_db, "exam1")
        _seed_key(fake_db, "exam1")
        r = _client(app_module).post("/api/exams/grade",
                                      json={"examId": "exam1", "attemptId": "att1", "answers": {"0": "B", "1": "A"}})
        body = r.get_json()
        assert body["percentage"] == 50.0
        assert body["status"] == "FAIL"
        assert "Arithmetic" in body["weakTopics"]

    def test_regrading_an_already_submitted_attempt_is_idempotent(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        self._seed_started(fake_db)
        _seed_exam(fake_db, "exam1")
        _seed_key(fake_db, "exam1")
        client = _client(app_module)
        first = client.post("/api/exams/grade", json={"examId": "exam1", "attemptId": "att1", "answers": {"0": "B", "1": "B"}})
        # Second call with a *different* (would-be higher) answer payload -
        # if grading re-ran on the new payload, the score would change.
        second = client.post("/api/exams/grade", json={"examId": "exam1", "attemptId": "att1", "answers": {"0": "wrong", "1": "wrong"}})
        assert first.get_json()["score"] == second.get_json()["score"]
        assert second.get_json()["alreadySubmitted"] is True

    def test_expired_submission_uses_autosaved_answers_not_late_payload(self, fake_db, app_module, monkeypatch):
        """Past the deadline, the browser's just-submitted payload must be
        ignored in favor of whatever was last autosaved server-side -
        otherwise a student could keep answering after time is up."""
        require_user_as(app_module, monkeypatch, uid="student-1")
        past_deadline = datetime.now(timezone.utc) - timedelta(minutes=10)
        fake_db.seed("examAttempts", "att1", {"userId": "student-1", "examId": "exam1", "status": "started",
                                               "deadlineAt": past_deadline, "passMark": 50,
                                               "answers": {"0": "B", "1": "A"}})  # autosaved: 1 correct
        _seed_exam(fake_db, "exam1")
        _seed_key(fake_db, "exam1")
        r = _client(app_module).post("/api/exams/grade",
                                      json={"examId": "exam1", "attemptId": "att1", "answers": {"0": "B", "1": "B"}})  # late payload: 2 correct
        body = r.get_json()
        assert body["expiredSubmission"] is True
        assert body["score"] == 1  # scored off the autosaved answers, not the late payload

    def test_grading_notifies_verified_parents(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        self._seed_started(fake_db)
        _seed_exam(fake_db, "exam1")
        _seed_key(fake_db, "exam1")
        fake_db.seed("parentChildLinks", "link1", {"childUid": "student-1", "parentUid": "parent-1", "status": "verified"})
        r = _client(app_module).post("/api/exams/grade",
                                      json={"examId": "exam1", "attemptId": "att1", "answers": {"0": "B", "1": "B"}})
        assert r.status_code == 200
        notifications = list(fake_db.dump("notifications").values())
        assert any(n["targetUid"] == "parent-1" and n["kind"] == "exam_result" for n in notifications)


class TestExamHistoryAndResult:
    def test_result_not_found(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        r = _client(app_module).get("/api/exams/result/nope")
        assert r.status_code == 404

    def test_result_wrong_owner_is_forbidden(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-2")
        fake_db.seed("examAttempts", "att1", {"userId": "student-1", "status": "submitted"})
        r = _client(app_module).get("/api/exams/result/att1")
        assert r.status_code == 403

    def test_result_not_yet_submitted_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        fake_db.seed("examAttempts", "att1", {"userId": "student-1", "status": "started"})
        r = _client(app_module).get("/api/exams/result/att1")
        assert r.status_code == 409

    def test_result_includes_per_question_review(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        fake_db.seed("examAttempts", "att1", {"userId": "student-1", "examId": "exam1", "status": "submitted",
                                               "score": 1, "totalPoints": 2, "percentage": 50, "passMark": 50,
                                               "answers": {"0": "B", "1": "A"}})
        _seed_exam(fake_db, "exam1")
        _seed_key(fake_db, "exam1")
        r = _client(app_module).get("/api/exams/result/att1")
        assert r.status_code == 200
        review = r.get_json()["review"]
        assert review[0]["isCorrect"] is True
        assert review[1]["isCorrect"] is False
        assert review[1]["correctAnswer"] == "B"

    def test_history_lists_only_own_submitted_attempts(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        fake_db.seed("examAttempts", "att1", {"userId": "student-1", "status": "submitted", "examTitle": "Midterm",
                                               "submittedAt": datetime.now(timezone.utc)})
        fake_db.seed("examAttempts", "att2", {"userId": "other", "status": "submitted"})
        fake_db.seed("examAttempts", "att3", {"userId": "student-1", "status": "started"})
        r = _client(app_module).get("/api/exams/history")
        ids = [a["attemptId"] for a in r.get_json()["attempts"]]
        assert ids == ["att1"]
