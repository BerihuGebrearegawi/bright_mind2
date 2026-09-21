"""Lightweight Gemini embedding layer for Bright Mind Tutor.

Embeddings are stored alongside AI chunks in Firestore so the platform does not
need a separate paid vector database. Retrieval uses semantic similarity over a
bounded candidate set (Grade + Subject) and falls back to lexical ranking when
embeddings are unavailable.
"""
import math
import os
from typing import Iterable

import requests


EMBEDDING_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
DEFAULT_MODEL = "gemini-embedding-001"
DEFAULT_DIM = 768
MAX_BATCH = 100


def _api_key():
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    return key


def embed_texts(texts: Iterable[str], task_type="RETRIEVAL_DOCUMENT") -> list[list[float]]:
    values = [str(x or "").strip() for x in texts]
    if not values:
        return []
    model = os.getenv("GEMINI_EMBEDDING_MODEL", DEFAULT_MODEL)
    dim = int(os.getenv("GEMINI_EMBEDDING_DIM", str(DEFAULT_DIM)))
    results = []
    for start in range(0, len(values), MAX_BATCH):
        batch = values[start:start + MAX_BATCH]
        payload = {
            "requests": [
                {
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": text[:12000]}]},
                    "taskType": task_type,
                    "outputDimensionality": dim,
                }
                for text in batch
            ]
        }
        response = requests.post(
            EMBEDDING_URL.format(model=model),
            headers={"x-goog-api-key": _api_key(), "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        data = response.json()
        if not response.ok:
            err = data.get("error", {})
            raise RuntimeError(err.get("message", "Gemini embedding request failed."))
        for item in data.get("embeddings", []):
            values_out = item.get("values") or []
            if not values_out:
                raise RuntimeError("Gemini returned an empty embedding.")
            results.append([float(v) for v in values_out])
    if len(results) != len(values):
        raise RuntimeError("Gemini returned an incomplete embedding batch.")
    return results


def cosine_similarity(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)
