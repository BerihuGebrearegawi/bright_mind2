"""One-time migration for existing Bright Mind Tutor AI chunks.

Run on the server after setting FIREBASE_SERVICE_ACCOUNT_JSON and GEMINI_API_KEY:
    python backfill_ai_embeddings.py

It only adds missing embeddings; existing embeddings are left untouched.
"""
import os
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, firestore

from embedding_service import embed_texts


def get_db():
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        import json
        cred = credentials.Certificate(json.loads(raw))
    else:
        cred = credentials.ApplicationDefault()
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(cred)
    return firestore.client()


def main():
    db = get_db()
    docs = list(db.collection("aiMaterialChunks").stream())
    pending = [d for d in docs if not (d.to_dict() or {}).get("embedding")]
    print(f"Found {len(pending)} chunks without embeddings out of {len(docs)} total.")
    model = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    for start in range(0, len(pending), 100):
        batch_docs = pending[start:start + 100]
        texts = []
        for snap in batch_docs:
            data = snap.to_dict() or {}
            texts.append(f"{data.get('title', '')}\n{data.get('content', '')}")
        vectors = embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")
        batch = db.batch()
        now = datetime.now(timezone.utc)
        for snap, vector in zip(batch_docs, vectors):
            batch.update(snap.reference, {"embedding": vector, "embeddingModel": model, "embeddingUpdatedAt": now})
        batch.commit()
        print(f"Embedded {min(start + len(batch_docs), len(pending))}/{len(pending)}")


if __name__ == "__main__":
    main()
