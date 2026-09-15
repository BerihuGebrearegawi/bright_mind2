# Bright Mind Tutor V24 — Notification & Communication Center 2.0

V24 builds on V23 without replacing the existing notification center.

## Added
- Server-authoritative notification preferences.
- Secure FCM device-token registration through Flask.
- SHA-256 token document IDs instead of raw FCM tokens as Firestore IDs.
- Targeted admin announcements by student UID or grade.
- Optional notification `actionUrl` for deep-linking from the bell.
- Firestore rules now block direct browser writes to `fcmTokens` and notification preferences.

## Existing behavior preserved
- In-app notification feed and unread badge.
- Payment notifications.
- Live-class and Q&A notifications.
- Optional FCM push via Firebase Admin SDK.
- Google Drive / Cloudinary / ImgBB storage architecture.

## Environment
- `FCM_VAPID_KEY` remains the web push public key.
- `ENABLE_FCM_PUSH=1` enables server-side push sending.
- Firebase Admin credentials must already be configured for server-side notification features.

## Deployment note
The targeted grade announcement endpoint assumes student profile documents are in `users/{uid}` with a `grade` field. If the existing project uses another canonical profile collection, change that one query rather than duplicating student records.
