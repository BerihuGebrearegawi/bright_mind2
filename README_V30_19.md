# Bright Mind Tutor V30.19 — Student Live Class Experience

## Objective
Connect the existing live-class scheduler to the student's learning experience.

## Student flow
1. Authenticate student.
2. Load today's and upcoming live classes through the Flask API.
3. Filter by the student's authorized grade/class group.
4. Show class title, teacher, subject, start time, duration, and status.
5. Show Join only when the class is joinable.
6. Record attendance server-side.
7. After class completion, expose the recording only when the student is authorized.
8. Generate/attach AI summary and practice material when available.

## Recommended API contract

GET /api/live-classes/student
- Returns only classes the authenticated student is authorized to see.
- Supports date/status filters.
- Server determines eligibility.

POST /api/live-classes/<class_id>/join
- Validates enrollment and class timing.
- Creates/updates an attendance session server-side.
- Returns a short-lived join token/link when supported.

POST /api/live-classes/<class_id>/leave
- Records leave time and updates attendance.

GET /api/live-classes/<class_id>/attendance
- Student may see only their own attendance record.

GET /api/live-classes/<class_id>/recording
- Requires authorized enrollment and completed class.

## UI states

UPCOMING
- Countdown
- Reminder
- Details

JOINABLE
- Join Live Class

LIVE
- Join Live Class
- Attendance indicator

COMPLETED
- Recording (if published)
- AI Summary (if published)
- Practice Questions (if published)

## Security requirements
- Never trust grade/classGroupId supplied by the browser.
- Never expose another student's attendance.
- Never expose recording URLs before authorization.
- Join links/tokens should be short-lived.
- Attendance timestamps must be generated server-side.
- The student cannot mark themselves present by directly writing Firestore.
- All class access checks must use authenticated server-side identity.

## Learning loop

Live Class
  -> Attendance
  -> Recording
  -> AI Summary
  -> Practice
  -> Assessment
  -> Progress
