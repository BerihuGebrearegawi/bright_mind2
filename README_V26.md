# Bright Mind Tutor V26 — Admin Control Center 2.0

## Added
- Server-authoritative `/api/admin/control-center` overview endpoint.
- Aggregated counts for students, teachers, courses, exams, live classes, payments, support tickets and AI usage records.
- Payment, teacher-request and support health summaries.
- Recent platform activity feed.
- Admin audit log collection and protected audit endpoint.
- Audit entries for payment approval/rejection and announcement creation.
- Admin dashboard Control Center with refresh and quick navigation.

## Security
- All V26 control-center endpoints require the existing Firebase ID-token admin check.
- `adminAuditLogs` is server-authoritative and denied to browser clients by Firestore rules.
- Existing Google Drive, Cloudinary, ImgBB and Firestore architecture is preserved.

## Important
The dashboard reads through Flask rather than requiring the browser to enumerate all platform collections. Counts are capped at 5,000 documents per collection to keep the endpoint bounded. For a much larger production deployment, migrate counts to scheduled counters or Firestore aggregation queries.
