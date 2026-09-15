"""Real behavioural tests for app.py's AI gateway routes.

/api/ai/translate and /api/ai/homework-image call Gemini directly (same
generateContent+candidates shape as smart_quiz_scanner_routes.py, so both
reuse FakeGeminiAPI). /api/ai/tutor, /api/ai/homework-coach,
/api/ai/study-plan, and /api/ai/practice-quiz instead delegate to
ai_tutor_service's shared quota-gated pattern (check_and_record_quota ->
retrieve_material_context -> ask_gemini/generate_practice_quiz), so those
are tested by mocking that service layer rather than Gemini itself - the
point being to verify app.py's own gating/error mapping, not
ai_tutor_service's internals (covered directly and in depth in
tests/test_ai_tutor_service.py).
"""
import pytest

from tests.fakes import FakeGeminiAPI


def require_user_as(app_module, monkeypatch, uid="user-1", **extra):
    detail = {"uid": uid, **extra}
    monkeypatch.setattr(app_module, "_require_user_bearer", lambda: (True, detail))
    return detail


def require_user_denied(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "_require_user_bearer",
                         lambda: (False, ({"error": "Sign in required."}, 401)))


def _client(app_module):
    return app_module.app.test_client()


class TestAiTranslate:
    def test_signed_out_is_rejected(self, app_module, monkeypatch):
        require_user_denied(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/translate", json={"text": "hi", "targetLanguage": "am"})
        assert r.status_code == 401

    def test_missing_fields_is_rejected(self, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/translate", json={"text": "hi"})
        assert r.status_code == 400

    def test_too_long_is_rejected(self, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/translate",
                                      json={"text": "x" * 12001, "targetLanguage": "am"})
        assert r.status_code == 413

    def test_not_configured_returns_503(self, app_module, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/translate", json={"text": "hi", "targetLanguage": "am"})
        assert r.status_code == 503

    def test_happy_path(self, app_module, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        require_user_as(app_module, monkeypatch)
        import requests as requests_module
        fake = FakeGeminiAPI(raw_text="ጤና ይስጥልኝ")
        monkeypatch.setattr(requests_module, "post", fake.post)
        r = _client(app_module).post("/api/ai/translate",
                                      json={"text": "Hello", "targetLanguage": "am"})
        assert r.status_code == 200
        assert r.get_json()["translation"] == "ጤና ይስጥልኝ"

    def test_gemini_failure_returns_502(self, app_module, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        require_user_as(app_module, monkeypatch)
        import requests as requests_module
        fake = FakeGeminiAPI(ok=False)
        monkeypatch.setattr(requests_module, "post", fake.post)
        r = _client(app_module).post("/api/ai/translate",
                                      json={"text": "Hello", "targetLanguage": "am"})
        assert r.status_code == 502


class TestAiTutor:
    def test_signed_out_is_rejected(self, app_module, monkeypatch):
        require_user_denied(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/tutor", json={"message": "hi"})
        assert r.status_code == 401

    def test_missing_message_is_rejected(self, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/tutor", json={})
        assert r.status_code == 400

    def test_too_long_is_rejected(self, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/tutor", json={"message": "x" * 8001})
        assert r.status_code == 413

    def test_quota_exceeded_returns_429(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (False, 0))
        r = _client(app_module).post("/api/ai/tutor", json={"message": "Explain photosynthesis"})
        assert r.status_code == 429

    def test_happy_path_returns_answer_and_sources(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 4))
        monkeypatch.setattr(ai_tutor_service, "retrieve_material_context",
                             lambda db_getter, grade, subject, message: [{"id": "m1", "title": "Ch 3", "grade": "7", "subject": "Math"}])
        monkeypatch.setattr(ai_tutor_service, "ask_gemini",
                             lambda message, history, grade, subject, materials: ("2 plus 2 is 4.", "gemini-test"))
        r = _client(app_module).post("/api/ai/tutor", json={"message": "What is 2+2?", "grade": "7", "subject": "Math"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["answer"] == "2 plus 2 is 4."
        assert body["remainingToday"] == 4
        assert body["sources"][0]["title"] == "Ch 3"

    def test_not_configured_raises_503(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 4))
        monkeypatch.setattr(ai_tutor_service, "retrieve_material_context", lambda *a, **k: [])

        def _raise(*a, **k):
            raise RuntimeError("AI Tutor is not configured.")
        monkeypatch.setattr(ai_tutor_service, "ask_gemini", _raise)
        r = _client(app_module).post("/api/ai/tutor", json={"message": "hi"})
        assert r.status_code == 503

    def test_unexpected_failure_returns_502_not_a_crash(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 4))
        monkeypatch.setattr(ai_tutor_service, "retrieve_material_context", lambda *a, **k: [])

        def _raise(*a, **k):
            raise ValueError("boom")
        monkeypatch.setattr(ai_tutor_service, "ask_gemini", _raise)
        r = _client(app_module).post("/api/ai/tutor", json={"message": "hi"})
        assert r.status_code == 502


class TestAiHomeworkCoach:
    def test_missing_question_is_rejected(self, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/homework-coach", json={})
        assert r.status_code == 400

    def test_quota_exceeded_returns_429(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (False, 0))
        r = _client(app_module).post("/api/ai/homework-coach", json={"question": "Solve 2x+3=7"})
        assert r.status_code == 429

    def test_happy_path(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 3))
        monkeypatch.setattr(ai_tutor_service, "retrieve_material_context", lambda *a, **k: [])
        monkeypatch.setattr(ai_tutor_service, "ask_gemini",
                             lambda message, history, grade, subject, materials: ("Step 1: subtract 3...", "gemini-test"))
        r = _client(app_module).post("/api/ai/homework-coach", json={"question": "Solve 2x+3=7"})
        assert r.status_code == 200


class TestAiHomeworkImage:
    def _image(self, content=b"\x89PNGfake", mime="image/png", name="hw.png"):
        import io
        return (io.BytesIO(content), name, mime)

    def test_signed_out_is_rejected(self, app_module, monkeypatch):
        require_user_denied(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/homework-image", data={}, content_type="multipart/form-data")
        assert r.status_code == 401

    def test_missing_image_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/homework-image", data={}, content_type="multipart/form-data")
        assert r.status_code == 400

    def test_unsupported_mime_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/homework-image",
                                      data={"image": self._image(mime="application/pdf", name="hw.pdf")},
                                      content_type="multipart/form-data")
        assert r.status_code == 400

    def test_empty_image_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/homework-image",
                                      data={"image": self._image(content=b"")},
                                      content_type="multipart/form-data")
        assert r.status_code == 400

    def test_quota_exceeded_returns_429(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (False, 0))
        r = _client(app_module).post("/api/ai/homework-image", data={"image": self._image()},
                                      content_type="multipart/form-data")
        assert r.status_code == 429

    def test_not_configured_returns_503(self, fake_db, app_module, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 5))
        r = _client(app_module).post("/api/ai/homework-image", data={"image": self._image()},
                                      content_type="multipart/form-data")
        assert r.status_code == 503

    def test_gemini_quota_429_is_propagated(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 5))
        import requests as requests_module
        fake = FakeGeminiAPI(ok=False, status_code=429)
        monkeypatch.setattr(requests_module, "post", fake.post)
        r = _client(app_module).post("/api/ai/homework-image", data={"image": self._image()},
                                      content_type="multipart/form-data")
        assert r.status_code == 429

    def test_gemini_rejection_returns_502(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 5))
        import requests as requests_module
        fake = FakeGeminiAPI(ok=False, status_code=500)
        monkeypatch.setattr(requests_module, "post", fake.post)
        r = _client(app_module).post("/api/ai/homework-image", data={"image": self._image()},
                                      content_type="multipart/form-data")
        assert r.status_code == 502

    def test_happy_path(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 5))
        import requests as requests_module
        fake = FakeGeminiAPI(raw_text="Step 1: ...")
        monkeypatch.setattr(requests_module, "post", fake.post)
        r = _client(app_module).post("/api/ai/homework-image",
                                      data={"image": self._image(), "question": "Please help"},
                                      content_type="multipart/form-data")
        assert r.status_code == 200
        assert r.get_json()["answer"] == "Step 1: ..."
        # The image bytes must actually reach Gemini as inline base64 data.
        sent_body = fake.calls[0]["json"]
        parts = sent_body["contents"][0]["parts"]
        assert any("inlineData" in p for p in parts)


class TestAiStudyPlan:
    def test_signed_out_is_rejected(self, app_module, monkeypatch):
        require_user_denied(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/study-plan", json={"goal": "improve"})
        assert r.status_code == 401

    def test_missing_goal_and_weak_topics_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/study-plan", json={})
        assert r.status_code == 400

    def test_quota_exceeded_returns_429(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (False, 0))
        r = _client(app_module).post("/api/ai/study-plan", json={"goal": "pass the exam"})
        assert r.status_code == 429

    def test_days_are_clamped_to_thirty(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 5))
        captured = {}

        def fake_ask(prompt, history, grade, subject, materials):
            captured["prompt"] = prompt
            return "Plan text", "gemini-test"
        monkeypatch.setattr(ai_tutor_service, "ask_gemini", fake_ask)
        r = _client(app_module).post("/api/ai/study-plan", json={"goal": "pass", "days": 500})
        assert r.status_code == 200
        assert "30-day" in captured["prompt"]

    def test_invalid_days_type_returns_400(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 5))
        r = _client(app_module).post("/api/ai/study-plan", json={"goal": "pass", "days": "not-a-number"})
        assert r.status_code == 400

    def test_happy_path(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 5))
        monkeypatch.setattr(ai_tutor_service, "ask_gemini",
                             lambda prompt, history, grade, subject, materials: ("Day 1: review...", "gemini-test"))
        r = _client(app_module).post("/api/ai/study-plan",
                                      json={"weakTopics": "fractions", "grade": "6", "subject": "Math", "days": 5})
        assert r.status_code == 200
        assert r.get_json()["plan"] == "Day 1: review..."

    def test_not_configured_returns_503(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 5))

        def _raise(*a, **k):
            raise RuntimeError("AI Tutor is not configured.")
        monkeypatch.setattr(ai_tutor_service, "ask_gemini", _raise)
        r = _client(app_module).post("/api/ai/study-plan", json={"goal": "pass"})
        assert r.status_code == 503


class TestAiPracticeQuiz:
    def test_signed_out_is_rejected(self, app_module, monkeypatch):
        require_user_denied(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/practice-quiz", json={"topic": "fractions"})
        assert r.status_code == 401

    def test_missing_topic_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/ai/practice-quiz", json={})
        assert r.status_code == 400

    def test_quota_exceeded_returns_429(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (False, 0))
        r = _client(app_module).post("/api/ai/practice-quiz", json={"topic": "fractions"})
        assert r.status_code == 429

    def test_no_matching_material_returns_404(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 5))
        monkeypatch.setattr(ai_tutor_service, "retrieve_material_context", lambda *a, **k: [])
        r = _client(app_module).post("/api/ai/practice-quiz", json={"topic": "an unmatched topic"})
        assert r.status_code == 404

    def test_happy_path(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 5))
        monkeypatch.setattr(ai_tutor_service, "retrieve_material_context",
                             lambda db_getter, grade, subject, message, limit=6: [{"id": "m1", "title": "Ch1"}])
        monkeypatch.setattr(ai_tutor_service, "generate_practice_quiz",
                             lambda message, grade, subject, materials, count: ([{"question": "2+2?", "options": ["1", "2", "3", "4"], "answerIndex": 3}], "gemini-test"))
        r = _client(app_module).post("/api/ai/practice-quiz", json={"topic": "addition", "count": 3})
        assert r.status_code == 200
        body = r.get_json()
        assert len(body["questions"]) == 1
        assert body["sources"][0]["title"] == "Ch1"

    def test_count_is_clamped_to_ten(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        import ai_tutor_service
        monkeypatch.setattr(ai_tutor_service, "check_and_record_quota", lambda db_getter, uid, premium: (True, 5))
        monkeypatch.setattr(ai_tutor_service, "retrieve_material_context",
                             lambda db_getter, grade, subject, message, limit=6: [{"id": "m1", "title": "Ch1"}])
        captured = {}

        def fake_generate(message, grade, subject, materials, count):
            captured["count"] = count
            return [], "gemini-test"
        monkeypatch.setattr(ai_tutor_service, "generate_practice_quiz", fake_generate)
        _client(app_module).post("/api/ai/practice-quiz", json={"topic": "x", "count": 999})
        assert captured["count"] == 10
