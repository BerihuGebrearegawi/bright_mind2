# Bright Mind Tutor — V16

Advanced Exam Engine 2.0.

- Server-authoritative exam attempts and grading remain in Flask.
- Student answers autosave every 10 seconds while an attempt is active.
- True/False questions are supported in addition to MCQ.
- Teachers can assign a topic to each question.
- Completed attempts store topic-level performance and weak-topic hints.
- Students see topic analysis immediately after submission.
- Detailed result endpoint: GET /api/exams/result/<attempt_id>.
- Existing Google Drive, Cloudinary, ImgBB, Firestore and AI Tutor architecture is preserved.
