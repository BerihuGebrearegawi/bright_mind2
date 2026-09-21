"""Real behavioural tests for learning_challenge_routes.py's question-bank
endpoints: teacher/admin gating for create and approve, the ownership rule
for delete (a teacher may remove only their own still-draft questions -
never one they created that's already approved, since it may already be
referenced by a live challenge), payload validation, and the ai-draft
endpoint's pre-network guard clauses.
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


VALID_QUESTION = {"question": "2+2?", "options": {"A": "3", "B": "4"}, "correctAnswer": "B"}


class TestCreateQuestionGating:
    def test_student_cannot_create(self, fake_db):
        fake_db.seed("users", "s1", {"accountType": "student"})
        client = _client(fake_db, "s1")
        r = client.post("/api/question-bank", json=VALID_QUESTION)
        assert r.status_code == 403

    def test_unapproved_teacher_cannot_create(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": False})
        client = _client(fake_db, "t1")
        r = client.post("/api/question-bank", json=VALID_QUESTION)
        assert r.status_code == 403

    def test_approved_teacher_creates_as_draft(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(fake_db, "t1")
        r = client.post("/api/question-bank", json=VALID_QUESTION)
        assert r.status_code == 201
        qid = r.get_json()["questionId"]
        assert fake_db.dump("questionBank")[qid]["status"] == "draft"

    def test_empty_payload_is_rejected(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(fake_db, "t1")
        r = client.post("/api/question-bank", json={"question": "", "options": {}, "correctAnswer": ""})
        assert r.status_code == 400

    def test_correct_answer_must_be_one_of_the_options(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(fake_db, "t1")
        r = client.post("/api/question-bank", json={"question": "Q?", "options": {"A": "x", "B": "y"}, "correctAnswer": "C"})
        assert r.status_code == 400


class TestApproveQuestion:
    def test_approving_marks_status_and_approver(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        fake_db.seed("questionBank", "q1", {"status": "draft"})
        client = _client(fake_db, "t1")
        r = client.post("/api/question-bank/q1/approve")
        assert r.status_code == 200
        assert fake_db.dump("questionBank")["q1"]["status"] == "approved"


class TestDeleteQuestionOwnership:
    def test_teacher_can_delete_own_draft(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        fake_db.seed("questionBank", "q1", {"status": "draft", "createdBy": "t1"})
        client = _client(fake_db, "t1")
        r = client.delete("/api/question-bank/q1")
        assert r.status_code == 200

    def test_teacher_cannot_delete_own_already_approved_question(self, fake_db):
        """Once approved, a question may already be referenced by a live
        challenge or practice session - only an admin can remove it."""
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        fake_db.seed("questionBank", "q1", {"status": "approved", "createdBy": "t1"})
        client = _client(fake_db, "t1")
        r = client.delete("/api/question-bank/q1")
        assert r.status_code == 403

    def test_teacher_cannot_delete_someone_elses_draft(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        fake_db.seed("questionBank", "q1", {"status": "draft", "createdBy": "t2"})
        client = _client(fake_db, "t1")
        r = client.delete("/api/question-bank/q1")
        assert r.status_code == 403

    def test_admin_can_delete_any_question_including_approved(self, fake_db):
        fake_db.seed("users", "admin1", {"accountType": "admin"})
        fake_db.seed("questionBank", "q1", {"status": "approved", "createdBy": "t2"})
        client = _client(fake_db, "admin1")
        r = client.delete("/api/question-bank/q1")
        assert r.status_code == 200

    def test_missing_question_returns_404(self, fake_db):
        fake_db.seed("users", "admin1", {"accountType": "admin"})
        client = _client(fake_db, "admin1")
        r = client.delete("/api/question-bank/nope")
        assert r.status_code == 404


class TestQuestionBankListingDefaultsToApproved:
    def test_draft_questions_are_hidden_by_default(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        fake_db.seed("questionBank", "q1", {"status": "approved", "subject": "Math"})
        fake_db.seed("questionBank", "q2", {"status": "draft", "subject": "Math"})
        client = _client(fake_db, "t1")
        r = client.get("/api/question-bank")
        assert r.status_code == 200
        assert len(r.get_json()["questions"]) == 1


class TestAiDraftGuardClauses:
    def test_missing_subject_and_topic_is_rejected(self, fake_db):
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(fake_db, "t1")
        r = client.post("/api/question-bank/ai-draft", json={})
        assert r.status_code == 400

    def test_missing_api_key_returns_503_without_a_network_call(self, fake_db, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        fake_db.seed("users", "t1", {"accountType": "teacher"})
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(fake_db, "t1")
        r = client.post("/api/question-bank/ai-draft", json={"subject": "Math"})
        assert r.status_code == 503
