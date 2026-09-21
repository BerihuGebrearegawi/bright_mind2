# Bright Mind Tutor V13 — Smart Notifications & Communication

## Added
- Durable in-app notifications in Firestore (`notifications` collection).
- Student notification bell with unread badge, read/read-all actions, refresh on visibility and 60s polling.
- Admin in-app announcement publisher.
- Optional FCM broadcast for announcements via `ENABLE_FCM_PUSH=1`.
- Automatic student notifications for payment submission, payment approval/unlock, and exam result availability.
- Firestore composite indexes for notification queries.

## Environment
- `FIREBASE_SERVICE_ACCOUNT_JSON` — required for server-side notification APIs.
- `ENABLE_FCM_PUSH=1` — optional; when enabled, announcements also attempt browser push delivery.
- `FCM_VAPID_KEY` — existing browser push configuration.

In-app notifications remain the durable source of truth even when browser push is unavailable or disabled.
