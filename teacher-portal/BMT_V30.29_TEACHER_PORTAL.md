# Bright Mind Tutor V30.29 — Teacher Portal

## Focus
Teacher portal workflow completion layer and end-to-end QA preparation.

## Teacher workflow

Login
→ Dashboard
→ My Classes
→ Class
→ Students
→ Lessons
→ Assignments
→ Assessments
→ Grade submissions
→ Analytics
→ Live Classes
→ Notifications

## Added
- Class-open hooks.
- Lesson editing/loading hooks.
- Assignment save/create/update hooks.
- Assessment save/create/update hooks.
- Submission grading hooks.
- Responsive teacher workspace and roster styling.

## Non-destructive integration
Existing functions are used when available:
- openClass / viewClass / loadClass
- openTeacherLesson / editLesson / loadLesson
- saveAssignment / createAssignment / updateAssignment
- saveAssessment / createAssessment / updateAssessment
- gradeSubmission / saveGrade / submitGrade

If those functions are unavailable, custom events are emitted rather than
replacing backend or business logic.

## QA requirements
1. Teacher authentication and authorization
2. Dashboard
3. Class list
4. Class opening
5. Student roster
6. Lesson creation/editing
7. Assignment creation/editing
8. Assessment creation/editing
9. Submission review
10. Grading
11. Analytics
12. Live-class access
13. Notifications
14. Mobile workflow

Teacher actions must be authorization-checked by the backend. UI visibility alone
must never be treated as a security control.
