# Bright Mind Tutor V19 — AI Tutor 2.0 + BMT Knowledge Base

- Server-side Gemini gateway remains protected by Firebase bearer authentication.
- AI retrieval returns source metadata (chunk/material id, title, grade, subject).
- Grounding prompt explicitly separates BMT evidence from general knowledge.
- New `POST /api/ai/practice-quiz` creates 1–10 MCQ practice questions from retrieved BMT material.
- Practice quiz answers are returned to the authenticated browser only for interactive practice; this is not a graded Firestore exam attempt.
- Existing Google Drive / Cloudinary / ImgBB storage is unchanged. Firestore stores searchable AI text/chunks, not the original binary files.
- AI quota is shared with the practice-quiz endpoint so a quiz cannot bypass the daily AI limit.
