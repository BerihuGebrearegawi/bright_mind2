# Bright Mind Tutor V25 — Student–Teacher Communication & Support Center

V25 adds a server-authoritative communication layer without changing the existing Google Drive, Cloudinary, ImgBB, Firebase Auth, AI Tutor, Live Classes, Exam Archive, Payment, or notification architecture.

## Added
- Student support tickets with categories and optional teacher assignment.
- Ticket message threads with student/teacher role enforcement.
- Teacher ticket inbox and safe ticket claiming.
- Student/teacher ticket closing.
- In-app notifications when a ticket is assigned or a new message arrives.
- 1–5 star feedback endpoint.
- Admin ticket and feedback moderation endpoints.
- Firestore rules deny direct browser access to communication collections.

## Collections
- `supportTickets`
- `supportMessages`
- `feedback`

## Security model
Browser -> Firebase ID token -> Flask -> ownership/teacher/admin check -> Firestore Admin SDK.

Students cannot write ticket status, assignment, sender role, feedback ownership, or notification records directly from the browser.
