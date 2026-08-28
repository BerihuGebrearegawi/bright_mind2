# Bright Mind Tutor V30.17 — Firestore Security Hardening

## Implemented
- `quizResults` browser writes disabled. Results are now written by Flask after the server verifies the quiz answer key.
- `chats` are no longer publicly readable. New messages require `senderUid == request.auth.uid`. Browser updates are limited to the `isRead` field.
- `notifications` updates are restricted to the recipient and the `read` field only.
- `liveClasses` direct browser reads/writes disabled. Students and teachers use Flask endpoints for authorization, grade filtering and ownership checks.
- Student quiz-result submission migrated from direct Firestore `addDoc()` to `/api/quiz-results`.
- Student chat messages now include `senderUid`.

## Security principle
Client code can request an action, but the server is authoritative for sensitive results, live-class access and persisted notification/chat mutations.
