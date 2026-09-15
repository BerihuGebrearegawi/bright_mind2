# Deployment Checklist (Render)

All of the variables below are declared in `render.yaml` with `sync: false`,
meaning Render will prompt for them but never store a default — set them in
the Render dashboard under **Environment**.

## Required for the app to start at all

| Variable | What it's for |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Firebase Admin SDK service account (paste the whole JSON key) |
| `FIREBASE_WEB_API_KEY` | Firebase web API key (from Firebase Console → Project Settings) |

Without these two, almost every route returns "Firebase server credentials
are not configured."

## File storage (Cloudinary — free tier, no credit card needed)

| Variable | What it's for |
|---|---|
| `CLOUDINARY_CLOUD_NAME` | From your Cloudinary dashboard |
| `CLOUDINARY_API_KEY` | From your Cloudinary dashboard |
| `CLOUDINARY_API_SECRET` | From your Cloudinary dashboard |

Powers: document/book uploads, Smart Quiz Scanner, AI material uploads,
Development Center video/book attachments, and organization logo upload.

Documents (books, Smart Quiz Scanner originals, AI material PDFs) are
uploaded as Cloudinary `private` assets. The app mints a fresh,
short-lived signed download link (`CLOUDINARY_SIGNED_URL_TTL_SECONDS`,
default 300 seconds / 5 minutes, max 3600) every time an authorized user
opens/downloads one - it is not a fixed field to store and reuse.

## Payments (Chapa)

| Variable | What it's for |
|---|---|
| `CHAPA_SECRET_KEY` | Chapa merchant secret key |
| `CHAPA_WEBHOOK_SECRET` | Verifies Chapa's payment webhook calls |

## AI Tutor (Gemini)

| Variable | What it's for |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio API key |
| `GEMINI_MODEL` | e.g. `gemini-2.0-flash` |
| `GEMINI_VISION_MODEL` | Model used for image-based tutor requests |

## Telegram — 3 separate bots

Create three bots with **@BotFather** first, then set:

| Variable | Bot |
|---|---|
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_WEBHOOK_SECRET` | Academic Challenge bot |
| `TELEGRAM_MODERATOR_BOT_TOKEN` / `TELEGRAM_MODERATOR_WEBHOOK_SECRET` | Moderator/Support bot |
| `TELEGRAM_PUBLISHER_BOT_TOKEN` / `TELEGRAM_PUBLISHER_WEBHOOK_SECRET` | Publisher/Content bot |

After setting each pair and deploying, use the Admin dashboard's Telegram
cards ("Set / Update Webhook") to register each bot's webhook with Telegram
— do this for all three, not just the first one.

For the Mini App button (`/start` on the Academic bot) to work, also set
`BMT_BASE_URL` to your deployed HTTPS URL (e.g.
`https://bright-mind-tutor.onrender.com`).

## Scheduled Telegram publishing (needs an external cron)

| Variable | What it's for |
|---|---|
| `BMT_SCHEDULER_SECRET` | Shared secret an external cron service sends back to prove it's authorized |

Render's free plan has no built-in cron. Scheduled posts sit in
`telegramSchedules` until something calls the tick endpoint. Use a free
service like **cron-job.org**:

- URL: `https://<your-app>.onrender.com/api/admin/telegram/scheduler/tick`
- Method: `POST`
- Header: `X-BMT-SCHEDULER-SECRET: <same value as BMT_SCHEDULER_SECRET>`
- Interval: every 5 minutes

Without this, a "scheduled" post will never actually send.

## Everything else (has safe defaults, override only if needed)

| Variable | Default |
|---|---|
| `PYTHON_VERSION` | 3.11.9 |
| `BMT_ENV` | `production` |
| `FLASK_DEBUG` | 0 |
| `TRUST_PROXY_HEADERS` | 1 |
| `PASSWORD_RESET_CONTINUE_URL` | — set to your site's reset-password page |
| `DIAGNOSTIC_KEY` | — set your own value to protect `/api/admin/diagnostics`-style routes |
| `PAYMENT_CURRENCY` | ETB |
| `TELEGRAM_MAX_MESSAGE_LENGTH` | 3500 |
| `ALLOWED_ORIGINS` | unset (no CORS headers on `/api/*`) — fine as-is since the app is server-rendered and same-origin; only set this (comma-separated origins) if you build a separate frontend/mobile client that calls this API cross-origin |

`/readyz` and `/api/admin/config-status` treat **Chapa** as the deployed
payment gateway (they check `CHAPA_SECRET_KEY`/`CHAPA_WEBHOOK_SECRET`, not
just `TELEBIRR_WEBHOOK_SECRET`/`CBE_WEBHOOK_SECRET`). Telebirr/CBE share the
same generic `/api/payments/webhook/<provider>` route and their env vars are
still supported if you wire up one of those gateways instead of or in
addition to Chapa, but a Chapa-only deployment set up exactly per the
"Payments (Chapa)" section above will correctly show as ready.

## Firestore indexes and rules

`firestore.indexes.json` and `firestore.rules` / `storage.rules` are not
picked up automatically by Render — they must be deployed separately via the
Firebase CLI (`firebase deploy --only firestore:indexes,firestore:rules`) or
pasted into the Firebase Console. If a query added in this update (e.g. the
Academic Challenge round-advancement check, which queries
`academicChallenges` by `season` + `roundNumber`) fails with a "the query
requires an index" error, either deploy `firestore.indexes.json` or click the
link Firestore includes in that error message to auto-create it.

## After every deploy, sanity-check

1. `GET /healthz` → should return 200
2. Log in to `/admin`, check the "Telegram Academic/Moderator/Publisher Bot"
   cards all show "🟢 Bot configured"
3. Try one document upload (e.g. a book or Smart Quiz scan) to confirm
   Cloudinary is wired up
4. Trigger the scheduler tick manually once to confirm the secret matches

## Final V31.108 deployment sequence (GitHub → Render)

### A. Termux / GitHub

1. Extract the final release ZIP into a clean directory.
2. Before committing, confirm that no `.env`, Firebase service-account JSON, private key, or other credential file is present:
   `find . -maxdepth 2 -type f \( -name '.env' -o -name '*.json' \) -print`
   Review every result before `git add`.
3. Install the declared production and test dependencies when running the full local test suite:
   `python -m pip install -r requirements.txt -r requirements-dev.txt`
4. Run the local static and test checks that do not require production credentials:
   `python -m compileall -q .`
   `pytest -q`
5. Firebase CLI deployment can now use the included `firebase.json` to map the existing rules/indexes files:
   `firebase deploy --only firestore:rules,firestore:indexes,storage`
   Authenticate/select the intended Firebase project before deploying; do not place service-account credentials in the repository.
6. Initialize or update the Git repository, then commit and push the exact release:
   `git add .`
   `git status`
   `git commit -m "BMT V31.108 final deployment release"`
   `git branch -M main`
   `git push -u origin main`

Do not paste Firebase service-account JSON, Chapa secrets, Gemini keys, Telegram bot
secrets, Cloudinary API secrets, or webhook secrets into GitHub. The public Firebase
Web API key in the browser Firebase configuration is a client identifier, not a
service-account credential; the Admin service-account key remains server-only.

### B. Render Blueprint deployment

1. In Render, choose **New → Blueprint** and connect the GitHub repository containing
   `render.yaml`.
2. Review the service name `bright-mind-tutor`, build command, start command, and
   health check before applying the Blueprint.
3. Enter all `sync: false` secrets when Render asks for them during initial Blueprint
   creation. For an existing service, verify/update them manually in **Environment**.
4. Deploy and wait for the build to finish.
5. Confirm `GET /healthz` returns HTTP 200 and reports `version: 31.108`.
6. Open `/readyz` after all required integrations are configured. A `503` here means
   one of the readiness checks is intentionally not satisfied; inspect its `checks`
   object rather than guessing from the browser page.

### C. Production verification order

Run these checks in this order after the first successful deploy:

1. `/healthz` — process is alive and the release version is correct.
2. Login as a normal student — Firebase authentication works.
3. Confirm a suspended test account is denied while an active account is allowed.
4. Admin login — admin-only endpoints reject non-admin users.
5. Manual payment: submit → Admin sees it → Approve → entitlement becomes premium.
6. Manual payment rejection: submit → Reject with reason → status becomes Rejected;
   retrying the same rejection must remain idempotent.
7. Confirm an approved/verified payment cannot subsequently be rejected.
8. Chapa: initialize a test payment and use the provider's real verification/webhook
   path before treating automatic payments as production-ready.
9. Cloudinary: upload one authorized document and open it through the app.
10. Telegram: configure each bot's webhook separately and verify its status card.
11. Service worker: test one online page load and one offline/static-cache scenario on
    a real Android device.

### D. Important production limitations

The current `render.yaml` uses Render's **Free** web-service plan. Free services can
spin down after 15 minutes without inbound traffic and the filesystem is ephemeral.
Therefore Firestore and Cloudinary are the persistent stores for application data and
uploaded files; do not rely on local files surviving restarts or deploys. For a real
production service with predictable latency, move the web service to a paid plan.

The generic Telebirr/CBE webhook adapter is signature-protected but is not a native
Telebirr/CBE API integration. A real provider account and end-to-end provider test are
still required before declaring those payment paths production-complete.
