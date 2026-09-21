"""Direct unit tests for ai_tutor_service.py - previously only exercised
indirectly (mocked as a black box) from app.py's AI route tests. These
tests call its functions directly and control `requests.post` themselves,
so they pin down the module's own behavior: quota accounting math, the
keyword-ranking fallback used when semantic embedding is unavailable, and
the Gemini Interactions API's two response shapes / retry-on-5xx-or-429
logic / error mapping - none of which app.py's tests ever exercised since
they mocked this module's functions directly.
"""
import json

import pytest

import ai_tutor_service as svc
from tests.fakes import FakeHttpResponse


# ---------------------------------------------------------------------------
# check_and_record_quota
# ---------------------------------------------------------------------------

class TestCheckAndRecordQuota:
    def test_no_db_getter_fails_closed(self, monkeypatch):
        """Without a way to track usage there is no way to enforce the daily
        limit, so quota tracking must fail closed (raise) rather than let
        the request through unmetered."""
        monkeypatch.setenv("AI_FREE_DAILY_LIMIT", "5")
        with pytest.raises(RuntimeError):
            svc.check_and_record_quota(None, "user-1", premium=False)

    def test_premium_gets_the_higher_limit(self, fake_db, monkeypatch):
        monkeypatch.setenv("AI_FREE_DAILY_LIMIT", "5")
        monkeypatch.setenv("AI_PREMIUM_DAILY_LIMIT", "40")
        allowed, remaining = svc.check_and_record_quota(lambda: fake_db, "user-1", premium=True)
        assert allowed is True
        assert remaining == 39

    def test_first_use_of_the_day_is_recorded_and_allowed(self, fake_db, monkeypatch):
        monkeypatch.setenv("AI_FREE_DAILY_LIMIT", "5")
        allowed, remaining = svc.check_and_record_quota(lambda: fake_db, "user-1", premium=False)
        assert allowed is True
        assert remaining == 4
        usage = list(fake_db.dump("aiUsage").values())[0]
        assert usage["messages"] == 1
        assert usage["uid"] == "user-1"

    def test_usage_accumulates_across_calls_same_day(self, fake_db, monkeypatch):
        monkeypatch.setenv("AI_FREE_DAILY_LIMIT", "3")
        db_getter = lambda: fake_db
        r1 = svc.check_and_record_quota(db_getter, "user-1", premium=False)
        r2 = svc.check_and_record_quota(db_getter, "user-1", premium=False)
        r3 = svc.check_and_record_quota(db_getter, "user-1", premium=False)
        assert [r[0] for r in (r1, r2, r3)] == [True, True, True]
        assert [r[1] for r in (r1, r2, r3)] == [2, 1, 0]

    def test_exceeding_the_limit_is_rejected_without_incrementing_further(self, fake_db, monkeypatch):
        monkeypatch.setenv("AI_FREE_DAILY_LIMIT", "1")
        db_getter = lambda: fake_db
        first = svc.check_and_record_quota(db_getter, "user-1", premium=False)
        second = svc.check_and_record_quota(db_getter, "user-1", premium=False)
        assert first == (True, 0)
        assert second[0] is False
        usage = list(fake_db.dump("aiUsage").values())[0]
        assert usage["messages"] == 1  # the rejected call never incremented

    def test_usage_is_isolated_per_user(self, fake_db, monkeypatch):
        monkeypatch.setenv("AI_FREE_DAILY_LIMIT", "1")
        db_getter = lambda: fake_db
        svc.check_and_record_quota(db_getter, "user-1", premium=False)
        allowed, remaining = svc.check_and_record_quota(db_getter, "user-2", premium=False)
        assert allowed is True
        assert remaining == 0

    def test_firestore_failure_fails_closed(self, monkeypatch):
        """If quota tracking itself breaks, the request must be rejected
        rather than let through unmetered - a fail-open quota would turn an
        infrastructure problem into unlimited paid AI usage."""
        monkeypatch.setenv("AI_FREE_DAILY_LIMIT", "5")

        def broken_db_getter():
            raise RuntimeError("Firestore is down")
        with pytest.raises(RuntimeError):
            svc.check_and_record_quota(broken_db_getter, "user-1", premium=False)


# ---------------------------------------------------------------------------
# retrieve_material_context
# ---------------------------------------------------------------------------

class TestRetrieveMaterialContext:
    def test_no_db_getter_returns_empty(self):
        assert svc.retrieve_material_context(None, "7", "Math", "question") == []

    def test_filters_by_grade_and_subject(self, fake_db):
        fake_db.seed("aiMaterialChunks", "c1", {"grade": "7", "subject": "Math", "content": "Algebra basics", "title": "Ch1"})
        fake_db.seed("aiMaterialChunks", "c2", {"grade": "8", "subject": "Math", "content": "Geometry basics", "title": "Ch2"})
        results = svc.retrieve_material_context(lambda: fake_db, "7", "Math", "algebra")
        assert [r["id"] for r in results] == ["c1"]

    def test_inactive_chunks_are_excluded(self, fake_db):
        fake_db.seed("aiMaterialChunks", "c1", {"grade": "7", "content": "Algebra", "title": "Ch1", "active": False})
        results = svc.retrieve_material_context(lambda: fake_db, "7", "", "algebra")
        assert results == []

    def test_empty_content_chunks_are_excluded(self, fake_db):
        fake_db.seed("aiMaterialChunks", "c1", {"grade": "7", "content": "", "title": "Ch1"})
        results = svc.retrieve_material_context(lambda: fake_db, "7", "", "algebra")
        assert results == []

    def test_keyword_fallback_ranks_by_matching_words(self, fake_db, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # embed_texts raises -> falls back
        fake_db.seed("aiMaterialChunks", "c1", {"grade": "7", "content": "Photosynthesis happens in chloroplasts", "title": "Biology"})
        fake_db.seed("aiMaterialChunks", "c2", {"grade": "7", "content": "Quadratic equations and roots", "title": "Algebra"})
        results = svc.retrieve_material_context(lambda: fake_db, "7", "", "How does photosynthesis work in chloroplasts?")
        assert results[0]["title"] == "Biology"

    def test_no_query_keywords_returns_all_candidates_unranked(self, fake_db, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        fake_db.seed("aiMaterialChunks", "c1", {"grade": "7", "content": "Anything", "title": "A"})
        results = svc.retrieve_material_context(lambda: fake_db, "7", "", "!!!")  # no >=3-char words
        assert len(results) == 1

    def test_respects_limit(self, fake_db, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        for i in range(10):
            fake_db.seed("aiMaterialChunks", f"c{i}", {"grade": "7", "content": f"topic number {i} words", "title": f"T{i}"})
        results = svc.retrieve_material_context(lambda: fake_db, "7", "", "topic words", limit=3)
        assert len(results) == 3

    def test_semantic_path_is_used_when_embeddings_available(self, fake_db, monkeypatch):
        """When embedding_service works, its ranking wins over keyword
        overlap even when keyword overlap would have picked the other
        document - proving the semantic branch actually short-circuits the
        keyword fallback rather than just running unused."""
        fake_db.seed("aiMaterialChunks", "c1", {"grade": "7", "content": "shares zero keywords with the query", "title": "SemanticWinner",
                                                 "embedding": [1.0, 0.0]})
        fake_db.seed("aiMaterialChunks", "c2", {"grade": "7", "content": "query query query keyword overlap", "title": "KeywordWinner",
                                                 "embedding": [0.0, 1.0]})
        import embedding_service
        monkeypatch.setattr(embedding_service, "embed_texts", lambda texts, task_type=None: [[1.0, 0.0]])
        results = svc.retrieve_material_context(lambda: fake_db, "7", "", "query query query")
        assert results[0]["title"] == "SemanticWinner"

    def test_db_exception_returns_empty_not_a_crash(self, monkeypatch):
        def broken():
            raise RuntimeError("boom")
        assert svc.retrieve_material_context(broken, "7", "", "x") == []


# ---------------------------------------------------------------------------
# ask_gemini
# ---------------------------------------------------------------------------

def _interactions_response(status_code=200, output_text=None, steps=None, headers=None):
    data = {}
    if output_text is not None:
        data["output_text"] = output_text
    if steps is not None:
        data["steps"] = steps
    return FakeHttpResponse(status_code=status_code, json_data=data, headers=headers)


class TestAskGemini:
    def test_missing_api_key_raises_immediately(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(RuntimeError):
            svc.ask_gemini("hi", [], "7", "Math", [])

    def test_output_text_shape_is_parsed(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "post",
                             lambda *a, **k: _interactions_response(output_text="The answer is 4."))
        answer, model = svc.ask_gemini("2+2?", [], "7", "Math", [])
        assert answer == "The answer is 4."

    def test_steps_shape_is_parsed(self, monkeypatch):
        """Covers the May-2026 breaking-change response shape (see the
        comment in _gemini_headers) where there is no output_text at all."""
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        import requests as requests_module
        steps = [{"type": "model_output", "content": [{"text": "Step-by-step answer."}]}]
        monkeypatch.setattr(requests_module, "post", lambda *a, **k: _interactions_response(steps=steps))
        answer, model = svc.ask_gemini("2+2?", [], "7", "Math", [])
        assert answer == "Step-by-step answer."

    def test_empty_answer_raises(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("GEMINI_MAX_ATTEMPTS", "1")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "post", lambda *a, **k: _interactions_response(output_text=""))
        with pytest.raises(RuntimeError):
            svc.ask_gemini("2+2?", [], "7", "Math", [])

    def test_invalid_json_response_raises(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("GEMINI_MAX_ATTEMPTS", "1")
        import requests as requests_module

        class _BadJsonResponse(FakeHttpResponse):
            def json(self):
                raise ValueError("not json")
        monkeypatch.setattr(requests_module, "post", lambda *a, **k: _BadJsonResponse(status_code=200))
        with pytest.raises(RuntimeError):
            svc.ask_gemini("2+2?", [], "7", "Math", [])

    def test_client_error_is_not_retried(self, monkeypatch):
        """A 400 (request rejected) should fail on the first attempt, not
        burn through retries meant for transient 429/5xx failures."""
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("GEMINI_MAX_ATTEMPTS", "3")
        calls = []
        import requests as requests_module

        def fake_post(*a, **k):
            calls.append(1)
            return _interactions_response(status_code=400)
        monkeypatch.setattr(requests_module, "post", fake_post)
        with pytest.raises(RuntimeError):
            svc.ask_gemini("2+2?", [], "7", "Math", [])
        assert len(calls) == 1

    def test_retries_on_429_then_succeeds(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("GEMINI_MAX_ATTEMPTS", "2")
        import requests as requests_module
        calls = []

        def fake_post(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                return _interactions_response(status_code=429, headers={"Retry-After": "0"})
            return _interactions_response(output_text="Recovered answer.")
        monkeypatch.setattr(requests_module, "post", fake_post)
        answer, model = svc.ask_gemini("2+2?", [], "7", "Math", [])
        assert answer == "Recovered answer."
        assert len(calls) == 2

    def test_exhausting_retries_on_5xx_raises(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("GEMINI_MAX_ATTEMPTS", "2")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "post", lambda *a, **k: _interactions_response(status_code=503))
        with pytest.raises(RuntimeError):
            svc.ask_gemini("2+2?", [], "7", "Math", [])

    def test_network_exception_is_retried_then_raises(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("GEMINI_MAX_ATTEMPTS", "2")
        import requests as requests_module

        def fake_post(*a, **k):
            raise requests_module.exceptions.ConnectionError("down")
        monkeypatch.setattr(requests_module, "post", fake_post)
        with pytest.raises(RuntimeError):
            svc.ask_gemini("2+2?", [], "7", "Math", [])

    def test_system_prompt_includes_supplied_materials(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        import requests as requests_module
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["payload"] = json
            return _interactions_response(output_text="ok")
        monkeypatch.setattr(requests_module, "post", fake_post)
        materials = [{"title": "Ch3: Fractions", "content": "A fraction is..."}]
        svc.ask_gemini("What is a fraction?", [], "5", "Math", materials)
        assert "Ch3: Fractions" in captured["payload"]["system_instruction"]
        assert "A fraction is..." in captured["payload"]["system_instruction"]


# ---------------------------------------------------------------------------
# generate_practice_quiz
# ---------------------------------------------------------------------------

def _quiz_response(questions):
    return _interactions_response(output_text=json.dumps({"questions": questions}))


VALID_Q = {"question": "2+2?", "options": ["1", "2", "3", "4"], "answerIndex": 3,
           "explanation": "Basic addition.", "sourceIndex": 1}


class TestGeneratePracticeQuiz:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(RuntimeError):
            svc.generate_practice_quiz("fractions", "7", "Math", [], count=3)

    def test_happy_path_returns_valid_questions(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "post", lambda *a, **k: _quiz_response([VALID_Q]))
        questions, model = svc.generate_practice_quiz("addition", "3", "Math", [{"title": "T", "content": "C"}], count=1)
        assert len(questions) == 1
        assert questions[0]["answerIndex"] == 3

    def test_json_wrapped_in_prose_is_extracted_via_regex_fallback(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        import requests as requests_module
        wrapped = "Here is the quiz:\n" + json.dumps({"questions": [VALID_Q]}) + "\nHope that helps!"
        monkeypatch.setattr(requests_module, "post", lambda *a, **k: _interactions_response(output_text=wrapped))
        questions, model = svc.generate_practice_quiz("addition", "3", "Math", [], count=1)
        assert len(questions) == 1

    def test_malformed_questions_are_filtered_out(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        import requests as requests_module
        bad_option_count = {"question": "Q1", "options": ["a", "b"], "answerIndex": 0}
        bad_index = {"question": "Q2", "options": ["a", "b", "c", "d"], "answerIndex": 9}
        missing_question_text = {"question": "", "options": ["a", "b", "c", "d"], "answerIndex": 0}
        monkeypatch.setattr(requests_module, "post",
                             lambda *a, **k: _quiz_response([bad_option_count, bad_index, missing_question_text, VALID_Q]))
        questions, model = svc.generate_practice_quiz("x", "3", "Math", [], count=10)
        assert len(questions) == 1
        assert questions[0]["question"] == "2+2?"

    def test_all_invalid_questions_raises(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "post",
                             lambda *a, **k: _quiz_response([{"question": "", "options": [], "answerIndex": 0}]))
        with pytest.raises(RuntimeError):
            svc.generate_practice_quiz("x", "3", "Math", [], count=5)

    def test_count_is_clamped_to_ten(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        import requests as requests_module
        many = [dict(VALID_Q, question=f"Q{i}") for i in range(15)]
        monkeypatch.setattr(requests_module, "post", lambda *a, **k: _quiz_response(many))
        questions, model = svc.generate_practice_quiz("x", "3", "Math", [], count=999)
        assert len(questions) == 10

    def test_non_json_non_recoverable_response_raises(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "post",
                             lambda *a, **k: _interactions_response(output_text="Sorry, I cannot help with that."))
        with pytest.raises(RuntimeError):
            svc.generate_practice_quiz("x", "3", "Math", [], count=5)
