# Bright Mind Tutor V30.28 — Student Portal

## Focus
Student portal workflow completion layer and end-to-end QA preparation.

## Student workflow

Login
→ Dashboard
→ Courses
→ Course
→ Lessons
→ Assignment
→ Submission
→ Assessment
→ Result
→ Progress
→ AI Tutor
→ Live Class
→ Notifications

## Added
- Student portal workflow integration layer.
- Course-open event/hooks.
- Lesson-open event/hooks.
- Assignment submission hooks.
- Assessment submission hooks.
- Accessible progress-bar semantics.
- Student dashboard/course/lesson styling.

## Non-destructive integration
The layer attempts to use existing functions when they exist:
- openCourse / viewCourse / loadCourse
- openLesson / viewLesson / loadLesson
- submitAssignment / saveAssignment / sendAssignment
- submitAssessment / finishAssessment / saveAssessment

If the existing implementation does not expose these functions, the layer emits
custom events instead of replacing business logic.

## QA status

The workflow layer is IMPLEMENTED.

A workflow must be marked COMPLETE only after actual application-level testing
with the project's real Firebase/API data.

Required tests:
1. Student login
2. Dashboard loading
3. Course list loading
4. Course opening
5. Lesson opening
6. Assignment loading
7. Assignment submission
8. Assessment loading
9. Assessment submission
10. Result display
11. Progress update
12. AI Tutor authorization
13. Live-class access authorization
14. Notifications
15. Mobile workflow

Any unavailable external credential or service must be marked UNVERIFIED.
