# Bright Mind Tutor V14 — Live Classes & Communication

V14 builds on V13 without replacing the existing Google Drive, Cloudinary, ImgBB, Firestore, AI Tutor, archive, exam and notification architecture.

## Added
- Server-authoritative Grade 7–12 live-class scheduling.
- Teacher-owned class edit/cancel API.
- Teacher online presence heartbeat with 120-second expiry.
- Student upcoming-class list with teacher online indicator.
- Class-specific Q&A submission and teacher notification.
- Reuses the existing in-app notification source of truth.
- Firestore rules deny direct client writes to live-class collections; Flask + Firebase Admin SDK owns mutations.

## Storage strategy
Live-class records contain metadata and a meeting/content URL only. Video/PDF binaries are not moved into Firestore. Existing Drive/Cloudinary/YouTube/Telegram strategy remains intact.

## Production environment
- `FIREBASE_SERVICE_ACCOUNT_JSON` must be configured on the server.
- Existing Firebase client configuration remains in the frontend.
- Use HTTPS in production.
- If a class needs video conferencing, supply a trusted HTTPS meeting URL (for example an approved classroom provider). The platform does not proxy live video through Flask.

## API
- `GET /api/live/classes`
- `POST /api/teacher/live/classes`
- `PATCH /api/teacher/live/classes/<class_id>`
- `POST /api/teacher/presence`
- `POST /api/live/classes/<class_id>/qna`
