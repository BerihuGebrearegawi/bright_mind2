# BMT V30.25 UI/UX Architecture

V30.25 is an additive UI/UX consistency layer on top of V30.24.

## Shared shell
Sidebar -> Topbar -> Page header -> Main content -> Mobile navigation.

## Design language
- Deep blue primary
- Gold accent
- White surfaces
- Soft neutral background
- Consistent spacing, radius, typography and controls
- Responsive desktop/tablet/mobile layouts

## Roles
Student: Dashboard, Courses, Live Classes, Assignments, Assessments, AI Tutor, Progress, Notifications, Profile.
Teacher: Dashboard, My Classes, Students, Lessons, Assignments, Assessments, Live Classes, Analytics, Notifications, Profile.
Parent: Dashboard, Children, Progress, Attendance, Assessments, Notifications, Payments, Profile.
Admin: Dashboard, Users, Teachers, Students, Courses, Payments, Live Classes, Notifications, Analytics, System Settings, Security.

## UX rules
- One clear primary action per page.
- Standard loading, success, error and empty states.
- Errors must be actionable and must not expose stack traces.
- Tables scroll on small screens.
- Keyboard focus remains visible.
- Reduced-motion preference is respected.

Existing business logic and API contracts must be preserved during migration.
