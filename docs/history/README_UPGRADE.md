# Bright Mind Tutor — Advanced Platform Upgrade

This version keeps the existing no-Firebase-Storage architecture:

- **Firestore**: users, courses, lessons, quizzes, exams, progress, payments, chats, notifications.
- **Google Drive**: documents.
- **Cloudinary**: video/audio.
- **ImgBB**: images.
- **Flask**: secure server gateway for AI and push delivery.

## New real features

### 1. AI Tutor
Student chat calls `POST /api/ai/tutor`. The AI provider key is **server-side only**.
Set on Render:

- `GEMINI_API_KEY` — Google AI Studio API key
- `GEMINI_MODEL` — optional; defaults to `gemini-2.5-flash`

The browser never receives the Gemini key.

### 2. Teacher Portal
Open `/teacher`.

- Students can apply to become teachers.
- Admin approves requests.
- Approved teachers can create courses.
- Teachers can publish timed exams.
- Teacher-owned data is protected by Firestore rules.

### 3. Secure Advanced Exams
Exam questions are public to authenticated students, but **answer keys are stored separately** in `examKeys` and are never sent to the student browser.

Exam submission calls `POST /api/exams/grade`, where the server grades the attempt using Firebase Admin credentials.

Required:

- `FIREBASE_SERVICE_ACCOUNT_JSON`

### 4. Push Notifications
Students can press **Enable notifications**. FCM tokens are stored in `fcmTokens`.

Set:

- `FCM_VAPID_KEY` — Firebase Web Push certificate key (public key)
- `FIREBASE_SERVICE_ACCOUNT_JSON` — server-side Firebase service account JSON

Admin can then send browser push notifications from the Admin Panel.

## Deployment

Do **not** put `GEMINI_API_KEY` or `FIREBASE_SERVICE_ACCOUNT_JSON` in JavaScript, HTML, Git, or `render.yaml`.
Use Render Environment Variables.

Before production:

1. Deploy Firestore rules and indexes.
2. Add the required Render environment variables.
3. Test `/healthz`.
4. Test AI Tutor with a student account.
5. Test teacher application → admin approval → teacher portal.
6. Create an exam and verify the student cannot read `examKeys`.
7. Test exam submission and result storage.
8. Enable browser notifications and send one test notification.


## 5. Server-authoritative Instant Access (new in this build)
Payment submission now goes through `POST /api/payments/submit` instead of allowing the browser to choose the amount/status. Admin approval goes through `POST /api/payments/approve` and is performed in one Firestore transaction:

1. payment -> `Approved`
2. `entitlements/{uid}` -> premium entitlement + expiry
3. `users/{uid}` -> compatibility `isPaid=true` + subscription

The student page listens to its user document and refreshes premium content as soon as the approval is written.

The `payments` and `entitlements` collections are no longer client-writable. This is important because a Firestore client can never be trusted to grant its own premium access.

### Payment gateway note
The current build intentionally separates **payment recording/approval** from the actual Telebirr/CBE gateway API. It accepts `telebirr`, `cbe`, or `manual` as a provider label, but it does not pretend to verify a gateway transaction without the provider's official API credentials/endpoint. When those credentials are available, the provider verification adapter can be connected before calling the same approval transaction.

## Step 2 — National Exam Archive

### Firestore collection: `examArchives`
Each document contains:
- `title`
- `grade`: 6–12
- `stream`: `Natural Sciences` / `Social Sciences` only for Grade 12
- `subject`
- `year`
- `isPremium`
- `storageProvider`: `google_drive`, `mega`, or `external`
- `fileId`
- `previewUrl`
- `downloadUrl`
- `createdAt`, `updatedAt`, `createdBy`

Students read this collection through Flask (`GET /api/exam-archive`), not directly from Firestore. Premium items return metadata with `locked: true` until the user's active entitlement exists.

Admins manage items through:
- `POST /api/admin/exam-archive`
- `PATCH /api/admin/exam-archive/<archiveId>`
- `DELETE /api/admin/exam-archive/<archiveId>`

For Google Drive PDFs, storing the Drive file ID is enough for the default in-app preview URL (`/file/<id>/preview`). The Drive file's sharing/access policy must still allow the intended authenticated viewer; the backend does not bypass Google Drive permissions.

## V30.34
V30.34 adds production readiness and observability improvements. Use `/healthz` for liveness and `/readyz` for deployment readiness. Configure `ALLOWED_ORIGINS` and server-side Firebase/Gemini credentials in the deployment environment.
