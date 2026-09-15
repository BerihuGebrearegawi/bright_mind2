"""Direct unit tests for embedding_service.py - flagged in the handoff
notes as untested at the HTTP-call level (previously only exercised
indirectly through ai_tutor_service.retrieve_material_context's semantic
path). These tests patch requests.post directly and never touch
ai_tutor_service, so they isolate embed_texts()/cosine_similarity()'s own
behavior: request shape, batching, and error handling.
"""
import pytest

from tests.fakes import FakeHttpResponse

import embedding_service


def _embeddings_response(vectors, status_code=200):
    return FakeHttpResponse(
        status_code=status_code,
        json_data={"embeddings": [{"values": v} for v in vectors]},
    )


class TestApiKeyRequired:
    def test_missing_api_key_raises_before_any_request(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            embedding_service.embed_texts(["hello"])


class TestEmptyInput:
    def test_empty_list_returns_empty_without_a_request(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        calls = []
        monkeypatch.setattr("requests.post", lambda *a, **k: calls.append(1) or _embeddings_response([[0.1]]))
        assert embedding_service.embed_texts([]) == []
        assert calls == []


class TestRequestShape:
    def test_sends_model_task_type_and_dimension(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
        monkeypatch.setenv("GEMINI_EMBEDDING_DIM", "768")
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _embeddings_response([[0.1, 0.2]])

        monkeypatch.setattr("requests.post", fake_post)
        embedding_service.embed_texts(["some text"], task_type="RETRIEVAL_QUERY")

        assert "gemini-embedding-001" in captured["url"]
        assert captured["headers"]["x-goog-api-key"] == "test-key"
        req = captured["json"]["requests"][0]
        assert req["taskType"] == "RETRIEVAL_QUERY"
        assert req["outputDimensionality"] == 768
        assert req["content"]["parts"][0]["text"] == "some text"

    def test_text_is_truncated_to_12000_chars(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        long_text = "x" * 20000
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            captured["text"] = json["requests"][0]["content"]["parts"][0]["text"]
            return _embeddings_response([[0.1]])

        monkeypatch.setattr("requests.post", fake_post)
        embedding_service.embed_texts([long_text])
        assert len(captured["text"]) == 12000

    def test_uses_default_model_and_dimension_when_env_unset(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.delenv("GEMINI_EMBEDDING_MODEL", raising=False)
        monkeypatch.delenv("GEMINI_EMBEDDING_DIM", raising=False)
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            captured["url"] = url
            captured["dim"] = json["requests"][0]["outputDimensionality"]
            return _embeddings_response([[0.1]])

        monkeypatch.setattr("requests.post", fake_post)
        embedding_service.embed_texts(["hi"])
        assert embedding_service.DEFAULT_MODEL in captured["url"]
        assert captured["dim"] == embedding_service.DEFAULT_DIM


class TestBatching:
    def test_more_than_max_batch_texts_are_split_across_requests(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        texts = [f"text-{i}" for i in range(embedding_service.MAX_BATCH + 5)]
        request_sizes = []

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            batch = json["requests"]
            request_sizes.append(len(batch))
            return _embeddings_response([[float(i)] for i in range(len(batch))])

        monkeypatch.setattr("requests.post", fake_post)
        results = embedding_service.embed_texts(texts)
        assert request_sizes == [embedding_service.MAX_BATCH, 5]
        assert len(results) == len(texts)


class TestErrorHandling:
    def test_upstream_error_response_raises_with_message(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        response = FakeHttpResponse(status_code=429, json_data={"error": {"message": "Rate limited"}})
        monkeypatch.setattr("requests.post", lambda *a, **k: response)
        with pytest.raises(RuntimeError, match="Rate limited"):
            embedding_service.embed_texts(["hello"])

    def test_upstream_error_without_message_uses_generic_fallback(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        response = FakeHttpResponse(status_code=500, json_data={})
        monkeypatch.setattr("requests.post", lambda *a, **k: response)
        with pytest.raises(RuntimeError, match="Gemini embedding request failed"):
            embedding_service.embed_texts(["hello"])

    def test_empty_embedding_values_raises(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        response = _embeddings_response([[]])
        monkeypatch.setattr("requests.post", lambda *a, **k: response)
        with pytest.raises(RuntimeError, match="empty embedding"):
            embedding_service.embed_texts(["hello"])

    def test_incomplete_batch_raises(self, monkeypatch):
        # Two texts requested, but the upstream response only returns one
        # embedding - a short-count response must not be silently accepted.
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        response = _embeddings_response([[0.1, 0.2]])
        monkeypatch.setattr("requests.post", lambda *a, **k: response)
        with pytest.raises(RuntimeError, match="incomplete"):
            embedding_service.embed_texts(["hello", "world"])

    def test_blank_and_none_texts_are_coerced_to_empty_strings_not_dropped(self, monkeypatch):
        # values = [str(x or "").strip() for x in texts] keeps list length
        # stable (important for zipping embeddings back to source chunks
        # upstream) even when some entries are None/blank.
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            captured["texts"] = [r["content"]["parts"][0]["text"] for r in json["requests"]]
            return _embeddings_response([[0.1], [0.2], [0.3]])

        monkeypatch.setattr("requests.post", fake_post)
        results = embedding_service.embed_texts([None, "  ", "real text"])
        assert captured["texts"] == ["", "", "real text"]
        assert len(results) == 3


class TestCosineSimilarity:
    def test_identical_vectors_are_similarity_one(self):
        assert embedding_service.cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_are_similarity_zero(self):
        assert embedding_service.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_are_similarity_negative_one(self):
        assert embedding_service.cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    @pytest.mark.parametrize("a,b", [
        ([], [1.0, 2.0]),
        ([1.0, 2.0], []),
        (None, [1.0, 2.0]),
        ([1.0, 2.0], None),
        ([1.0, 2.0], [1.0, 2.0, 3.0]),  # mismatched length
    ])
    def test_degenerate_inputs_return_zero_not_an_error(self, a, b):
        assert embedding_service.cosine_similarity(a, b) == 0.0

    def test_zero_vector_returns_zero_not_a_division_error(self):
        assert embedding_service.cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
