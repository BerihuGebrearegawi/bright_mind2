# Bright Mind Tutor V21 — National Exam Archive 2.0

## Scope
- National archive structure is restricted to Grade 6, Grade 8, and Grade 12.
- Grade 12 requires Natural Sciences or Social Sciences.
- Premium archive access is checked server-side. Existing `entitlements.premium` remains backward compatible; explicit `examArchive`, `features.examArchive`, or `products.national_exam_archive` can be used for granular access.
- Added `GET /api/exam-archive/<archive_id>` so opening one PDF does not require downloading the whole archive list.
- Premium URLs are not returned to locked students.
- Repaired Firestore rules so Live Classes, presence, and Q&A rules are inside the valid `/databases/{database}/documents` scope.

## Storage
Google Drive remains the primary PDF archive provider. Cloudinary/ImgBB remain unchanged for their existing media roles.
