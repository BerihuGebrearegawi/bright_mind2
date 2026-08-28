# Bright Mind Tutor — AI Tutor

## Environment
- `GEMINI_API_KEY` — server-only Gemini API key
- `GEMINI_MODEL` — defaults to `gemini-3.6-flash`
- `AI_FREE_DAILY_LIMIT` — defaults to 5
- `AI_PREMIUM_DAILY_LIMIT` — defaults to 40
- `FIREBASE_SERVICE_ACCOUNT_JSON` — required for authenticated server-side Firebase access

## Firestore
Optional teacher-published material chunks live in `aiMaterials/{materialId}`:
```json
{
  "title": "Quadratic Equations — Lesson 1",
  "grade": "10",
  "subject": "Mathematics",
  "content": "...text/chunk...",
  "active": true
}
```
The tutor retrieves a small set of grade/subject-matched chunks and ranks them by keyword overlap. This is intentionally a replaceable retrieval layer; a vector/semantic index can be added later without changing the `/api/ai/tutor` contract.

## Security
The Gemini key never reaches the browser. The route requires a Firebase ID token. Daily usage is recorded server-side in `aiUsage` and is best-effort if Firebase is temporarily unavailable.

## Gemini
The implementation uses the current Gemini Interactions API endpoint and keeps the model configurable.

## V10 — AI Material Manager

Teachers can publish lesson text or upload text-based PDFs from `/teacher`.
The backend extracts text with `pypdf`, normalizes it, splits it into overlapping
chunks, and stores metadata in `aiMaterials` plus searchable chunks in
`aiMaterialChunks`. The original PDF should continue to live in the existing
Google Drive/Cloudinary layer; Firestore stores the AI knowledge text, not the
binary archive.

Scanned/image-only PDFs require OCR before ingestion.

## V11 — Semantic Retrieval

AI chunks can now store Gemini embeddings in Firestore. The tutor first narrows
candidates by Grade and Subject, then ranks them by cosine similarity. If the
embedding API is unavailable, lexical retrieval remains the fallback.

Environment variables:
- `GEMINI_API_KEY` — server-side only
- `GEMINI_EMBEDDING_MODEL` — defaults to `gemini-embedding-001`
- `GEMINI_EMBEDDING_DIM` — defaults to `768`

Existing V10 chunks can be upgraded once with:
`python backfill_ai_embeddings.py`

No separate vector database is required for this bounded retrieval design.
Google documents Gemini embeddings as suitable for information retrieval and
supports reduced output dimensionality to reduce storage/computation. citeturn0search0turn0search3
