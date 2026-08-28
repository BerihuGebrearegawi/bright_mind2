# Bright Mind Tutor V18 — Personalized Learning Engine

## Added
- Personalized recommendations derived from existing submitted exam topic statistics.
- Weak-topic detection aggregated across completed exams.
- Student dashboard now shows "What should I study next?" recommendations.
- Recommendations distinguish high/medium/low priority.
- No new storage provider or paid database is required.
- Existing Google Drive, Cloudinary, ImgBB and Firebase architecture is preserved.

## Safety / architecture
- Recommendations are advisory and do not change grades or access permissions.
- The backend is the source of truth for analytics.
- Existing authentication remains required for `/api/student/progress`.
