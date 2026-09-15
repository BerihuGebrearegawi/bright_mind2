# Bright Mind Tutor (BMT)

An Ethiopian K-12 tutoring platform: Flask backend, Firebase (Auth +
Firestore) for data, Cloudinary for file storage, Chapa for payments,
Gemini for the AI Tutor, and a 3-bot Telegram platform (Academic,
Moderator, Publisher).

## Stack

- **Backend:** Flask 3 (`app.py` + one `*_routes.py` module per feature area)
- **Data:** Firebase Firestore, via `firebase-admin`
- **File storage:** Cloudinary (`cloudinary_storage.py`) — chosen because it
  needs no credit card / Google Cloud billing account
- **Payments:** Chapa (Ethiopian payment gateway)
- **AI:** Google Gemini (`ai_tutor_service.py`)
- **Telegram:** three separate bot identities — Academic Challenge
  (`telegram_challenge_routes.py`), Moderator/Support
  (`telegram_moderator_bot_routes.py`), Publisher/Content
  (`telegram_publisher_bot_routes.py`) — plus a shared admin platform
  (`telegram_platform_routes.py`, `telegram_targets_config.py`)
- **Frontend:** server-rendered Jinja templates (`templates/`) + plain JS
  (`static/`) — no build step

## Local structure

```
app.py                    Flask app, route registration, auth helpers
*_routes.py                One file per feature area (teacher, parent, awards, telegram, ...)
templates/                 admin.html, teacher.html, student.html, parent.html, index.html
static/                    Per-page JS + a few additive admin_*.js panels
docs/history/               Superseded READMEs and past AI-assistant session notes (not needed to run the app)
```

## Running

See `DEPLOYMENT.md` for the full Render environment-variable checklist.

```
pip install -r requirements.txt --break-system-packages
gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
```

## Notes for whoever picks this up next

- File storage is Cloudinary, not Google Cloud Storage. `cloudinary_storage.py`
  intentionally still returns `provider: "gcs"` in its response for backward
  compatibility with an existing Firestore permission check
  (`_can_access_gcs_book` in `app.py`) — see that module's docstring.
- Scheduled Telegram posts (`telegramSchedules`) do **not** send themselves.
  Something external must call `POST /api/admin/telegram/scheduler/tick`
  on a schedule (a free cron pinger works) — see `DEPLOYMENT.md`.
- `docs/history/` is a paper trail from past development sessions, kept for
  reference. Nothing in the running app reads from it.
