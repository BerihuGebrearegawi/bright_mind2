# Bright Mind Tutor V23 — Live EdTech System 2.0

## Added
- Grade 3–12 live class scheduling remains supported.
- Server-side teacher schedule conflict detection prevents overlapping classes for the same teacher.
- Student live-class enrollment endpoint.
- Grade-aware enrollment validation when a student profile has a grade.
- Attendance is recorded when an enrolled student joins a live class.
- Teacher enrollment-count endpoint for each owned live class.
- Student Live Classes UI now supports Enroll → Join → attendance flow.
- Existing Google Drive / Cloudinary / ImgBB storage architecture is unchanged.

## Security model
- All live endpoints require Firebase ID-token authentication.
- Only approved teachers can create/update classes or publish presence.
- Teachers can only manage classes they own.
- Students cannot mark attendance without enrollment.
- Direct client writes are not used for live-class enrollment/attendance.

## Deployment note
The Telebirr/CBE payment gateway and actual webhook credentials are intentionally not fabricated. V22 payment adapters must be connected to the real provider API specification before production use.
