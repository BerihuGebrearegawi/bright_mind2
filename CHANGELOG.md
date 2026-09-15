# Changelog (recent work)

Older version-by-version notes from past development sessions were moved to
`docs/history/` — they're historical record, not required reading. This file
tracks what actually changed recently, in plain terms.

## Storage migration: GCS → Cloudinary
- Google Cloud Storage required a billing account (credit card) that wasn't
  available, so document/book storage was moved to Cloudinary (free tier).
- `gcs_storage.py` is retired (moved to `docs/history/`); `cloudinary_storage.py`
  is now the only storage backend. See its docstring for why it still returns
  `provider: "gcs"` internally.

## Bug fixes
- `app.py`: document upload used a broken dynamic import
  (`__import__('firebase_admin').firestore`) that raised `AttributeError`
  whenever it was the first Firebase call in a worker process. Fixed.
- `admin_features_routes.py`: community chat audio messages crashed with
  `NameError: duration_seconds` — the field was read from the request but
  never validated/assigned. Fixed.
- `telegram_platform_routes.py`: the Analytics endpoint crashed with
  `NameError: mod` (typo for `mods`) on every call — it had never actually
  been exercised. Fixed, and an admin UI was added so it's visible now.
- `telegram_platform_routes.py`: the 12 canonical BMT channels/groups
  (`telegram_targets_config.py`) were defined but never actually loaded by
  the admin target list — only manually-added custom targets showed up.
  Fixed.
- `learning_challenge_routes.py`: the Challenge Leaderboard endpoint crashed
  with `NameError: _submission_epoch` on every call with submissions (the
  helper only existed in a different file, `award_ledger_routes.py`, and was
  never imported). Fixed by adding a local copy.

## Three-bot Telegram platform
- Merged in the Academic/Moderator/Publisher three-bot architecture (each
  bot has its own token/webhook secret) that had been built on an older
  code branch and never merged forward.
- Added admin "Set Webhook" controls for the Moderator and Publisher bots
  (previously only the Academic bot had one).
- Added: Telegram Analytics UI, Moderation Logs UI, a Media Library (reuse
  previously-published files), a Content Calendar view, moderator
  escalation (mute after repeated violations, then ban), a Telegram Mini
  App button on `/start`, and inline search (`@bot query`) for challenges.
- Added a "Smart Select" tool: pick a grade + purpose and the right
  channels/groups are auto-selected instead of picking all 12 by hand.
- Documented that scheduled posts require an external cron pinger — see
  `DEPLOYMENT.md`.

## New admin/teacher/student/parent features
- Admin: Active Teacher Roster (subjects, phone, activity — pulled from
  existing profile/course data, no new data entry required).
- Admin: per-student detail/history view (profile, payments, challenge
  attempts, awards, support tickets, linked parents) from one place.
- Student: profile completion (age, school, education level, guardian
  contact, address) and self-service region picker.
- Admin/Student/Parent: video and book attachments for the Psychology &
  Child Development lessons (previously text-only, no admin upload path).
- Regional Math/Aptitude competition system: region field, a scholarship
  award tier alongside cash prizes, and a full Admin/Teacher UI for
  question bank management and challenge lifecycle (create → publish →
  close → award policy → finalize → publish winners) — previously
  API-only with no UI at all.
- Round-advancement enforcement: previously, a challenge's "Qualification
  Count" only affected how the leaderboard was labeled — nothing stopped a
  non-qualifying student from starting the next round. The server now
  checks, for any Round N>1 challenge with a Season set, that the student
  ranked within the previous round's Qualification Count before allowing
  them to start. Requires a new Firestore composite index (`season` +
  `roundNumber` on `academicChallenges`) — see `DEPLOYMENT.md`.

## Repo hygiene
- Moved ~200 historical AI-session markdown/JSON files and 6 orphaned,
  unreferenced JS/PY files into `docs/history/`.
- Added this changelog, a current `README.md`, and `DEPLOYMENT.md`.

## Digital Library book creation & categories
- `admin_features_routes.py`'s book-creation endpoint always wrote to the
  `books` Firestore collection, even though `library_routes.py` supports 5
  categories (`books`, `bookLibrary`, `teacherGuides`, `referenceBooks`,
  `psychologyBooks`). The other 4 had no way to receive a book at all. Fixed:
  added a `collection` field to the create endpoint and a category dropdown
  in the admin "Upload Book" form.
- Added an `external_link` storage option so a book can be linked from any
  https:// URL (a school website, another host, an existing PDF link) —
  previously only a Cloudinary-domain URL or a Google Drive file ID were
  accepted.
- `firestore.rules` had no rule at all for `bookLibrary`, `teacherGuides`,
  `referenceBooks`, or `psychologyBooks` (only `books`), so client-side reads
  of those categories were silently denied by Firestore's default-deny even
  after the fixes above. Added matching rules for all four. **This file must
  be redeployed** (`firebase deploy --only firestore:rules`) for the fix to
  take effect — see `DEPLOYMENT.md`.
- The admin "Existing Books" list only ever queried the `books` collection;
  it now shows all 5 categories with a label, and Delete now targets the
  correct collection per book.

## New unified authentication entry point (`/auth`)
- Added a single email-first flow: enter email → the app checks (via
  Firebase's own `fetchSignInMethodsForEmail`) whether an account exists →
  returning users see a password field, new users see a role picker
  (Student / Teacher / Parent) → a tailored registration form per role →
  redirect straight to the right dashboard.
- Reuses existing account-creation mechanics exactly as-is: the same
  13-field `users/{uid}` document shape the existing dashboards already
  write (required by `firestore.rules`), the existing `/api/teacher/apply`
  endpoint for teacher applications (still gated on admin approval — a
  new teacher lands on a "pending approval" screen, not the dashboard),
  and the existing `/api/student/region` and `/api/student/profile-details`
  endpoints for the extra student fields (age, school, guardian, address).
- No backend changes were needed for this — it's a new front door only.
- `index.html`'s three role cards now link to `/auth?role=...` instead of
  straight to each dashboard.
- **Not done yet:** the 4 dashboards (`student.html`, `teacher.html`,
  `parent.html`, `admin.html`) each still have their own older, separate
  login/signup forms built in (email+password and phone+PIN, tab-switched).
  Those still work exactly as before and were not touched. `/auth` is the
  new recommended entry point, but a visitor who lands on `/student`
  directly (bookmark, old link) still sees the old inline form. Wiring
  each dashboard to redirect an unauthenticated visitor to `/auth` instead,
  and eventually retiring the duplicate inline forms, is a follow-on step.

## Dashboards now redirect to `/auth` instead of showing their own login form
- Addressed the item above: all 4 dashboards' "not signed in" branch now
  redirects to `/auth?role=...&next=<page>` instead of showing their own
  inline login/signup form. The old form markup is still present in each
  file (removing it is a separate, larger cleanup with more blast radius)
  but it's no longer reachable in normal use, since the redirect fires
  immediately on page load for anyone not signed in.
- This also sidesteps whatever bugs existed in the old per-page forms —
  everyone now goes through the one tested `/auth` flow.

## Student dashboard navigation and CSS cleanup
- Student dashboard's quick-nav only jumped to 5 of its 25+ sections
  (Overview, Library, Quiz, Exams, Chat) - AI Tutor, Live Classes, My
  Development, Videos, Books, Profile and others had no shortcut and
  required scrolling to find. 8 sections (Books, Videos, Playlist,
  Downloaded Videos, Quiz, Exams, Subscribe, Payment History) didn't even
  have an `id` to link to. Added ids and expanded the nav to 10 shortcuts
  covering the sections students use most.
- `static/style.css` (shared by all 4 dashboards) had **four** separate
  `:root` blocks added over time, two of which redefined the exact same
  variable names (`--primary`, `--success`, `--shadow-md`, etc.) with
  different values - meaning half the file's own color tokens were dead,
  silently overridden by a later block. Merged the two conflicting blocks
  into one, keeping the values that were actually taking effect (so this
  is a pure de-duplication with no visual change). A separate `--bmt-*`
  prefixed token set (added even later, for the same purpose) was left
  alone since it doesn't collide by name - unifying that with the main
  token set is a larger, separate follow-on task.

- Found and removed a wholesale duplicated CSS block: an entire "V31.22
  dashboard polish" rule set (hero layout, pulse animation, mobile media
  query) had been pasted twice in a row, once under each of two near-
  identical comment headers. Removed the second copy. Also aliased
  `--bmt-indigo` to `var(--primary)` since both held the exact same hex
  value under two different names.

## Admin dashboard navigation and a real duplicate-send bug
- Same problem as the student dashboard: the top nav only jumped to 4 of
  ~16 major sections, and a second, separate quick-link button row inside
  the Overview card (Payments/Exam Archive/Communication) duplicated that
  same purpose with different destinations. Expanded the top nav to 11
  destinations (Overview, Control Center, Students, Teachers, Digital
  Library, Quiz Scanner, Challenges, Growth Media, Telegram, Payments,
  Community) and added the two missing ids (`teacherRequestsSection`,
  `organizationLogoSection`) it needed to work.
- Found a real bug from the same duplication pattern: the "Send to
  community" button had `addEventListener('click', sendAdminCommunityMessage)`
  attached **twice**, in two separate `DOMContentLoaded` blocks - every
  click sent the admin's community message twice. Removed the duplicate.

## Teacher and Parent dashboards
- Teacher dashboard's nav had 8 destinations but was missing AI Materials,
  Live Classes, My Students, and Parent Messages - all real, already-built
  sections with no way to jump to them. Expanded to 12. No duplicate-line
  or duplicate-listener bugs were found in `teacher.html`/`teacher.js` -
  this dashboard was in noticeably better shape than student/admin.
- Parent dashboard had **no navigation at all** (the CSS for
  `.bmt-dashboard-nav` existed but no `<nav>` element used it). Added one
  with 6 destinations (Overview, My Students, Growth, Messages, Parent
  Learning, Payment). No duplicate-line or duplicate-listener bugs found
  here either.

## Live testing round: real bugs reported after deploying to Render
The person tested the deployed app directly and reported 11 issues. Each
was investigated in the actual code before fixing - not guessed at.

- **Dashboard Preview failing** - `ADMIN_PREVIEW_SECRET` was never in
  `render.yaml`, so `_make_preview_token()` always raised "not configured."
  Added the env var.
- **Upload Video / Upload Book doing nothing** - a real bug in `admin.js`:
  `(await import('./storage-service.js')).uploadX(...)` only awaits the
  *import*, not the upload call itself, so `result` was a pending Promise
  and `result.success` was always `undefined` - every upload silently
  "failed" regardless of whether the file actually uploaded. Fixed in all
  4 places this pattern appeared (book, video, quiz image, exam archive).
- **"Admin session expired" / "admin authentication required" on Digital
  Library, Live Stream, Telegram, Development Media, Challenge Manager** -
  the actual root cause: `admin.js` never set `window.auth`, only a
  module-scoped `const auth`. Every additive admin panel built this session
  (`dashboard_integrations.js`, `admin_telegram*.js`,
  `admin_development_content.js`, `admin_challenge_manager.js`) reads
  `window.auth.currentUser` to get a token - which was always `undefined`.
  `student.js` already did this correctly (`window.auth = auth`); `admin.js`
  never did. One-line fix, but it was silently breaking every admin panel
  built on top of admin.js since this pattern was introduced.
- **Teacher Requests / Teacher Roster / Parent-Student Linking crammed into
  one card** - split into three independent top-level cards, each with its
  own nav entry.
- **No way to delete a student/teacher/parent account** - added
  `DELETE /api/admin/users/<uid>` (removes the Firebase Auth account, their
  Firestore profile, and any teacher application record; refuses to delete
  admin accounts or your own account) plus a 🗑️ Delete button in the
  Student Directory and Teacher Roster, with a confirmation prompt.
- **Zone / sub-city / woreda not part of the region system** - added
  `zone` and `woreda` as optional fields alongside Region on the student
  profile and the Challenge Manager, so a competition round can be scoped
  down to a specific woreda, not just a whole region.
- **AI Copilot 500 error** - reviewed the endpoint in full; every failure
  path there returns 503/502 with a message, not a bare 500, so the exact
  cause couldn't be confirmed from code alone. Needs a Render log traceback
  to pin down.
- **Button labels not visible (Send/Play/Link, etc.)** - reviewed the
  button/color CSS in depth and found no rule that would hide button text;
  couldn't reproduce from code alone. Needs a screenshot of a specific
  button to diagnose.
- **Overall cross-dashboard integration** - acknowledged as a real,
  larger architectural question rather than a single bug; not something
  fixed in this pass.

## V31.106 merge: new teacher-side features + a repeat of an earlier bug

The person's own edits (V31.106) added real new functionality: an AI-assisted
question drafting endpoint (`/api/question-bank/ai-draft`), pushing approved
Question Bank questions to a class as classwork/groupwork/practice
(`/api/question-bank/assign`), sending questions to Telegram as native quiz
polls (`/api/telegram/publisher/send-quiz`), a public organization-logo
endpoint (`/api/settings/organization`) so student/teacher/parent dashboards
can show the org logo without an admin session, and a targets picker for the
new Telegram quiz feature (`/api/telegram/publisher/targets`). All of this
was reviewed and works correctly.

- Found the same class of bug fixed earlier for `telegram_platform_routes.py`,
  now reintroduced in the new `telegram_publisher_bot_routes.py`: its
  `_targets()`/`_target_by_id()` only read the `telegramTargets` Firestore
  collection (admin-added custom targets), not the 12 canonical BMT
  channels/groups from `telegram_targets_config.py`. Since a normal admin
  never bothers re-adding the 12 canonical targets as "custom" ones, the
  teacher-facing "Send to Telegram target" picker (used by the new
  ai-draft/send-quiz feature) would always show an empty or near-empty list.
  Fixed by merging in `BMT_TELEGRAM_TARGETS`, matching the earlier fix.
- Verified the earlier critical fixes from this session survived the merge:
  `window.auth = auth` in `admin.js`, the missing-`await` fix on all 4
  Cloudinary upload call sites, the de-duplicated CSS, and the
  double-listener fix on the community chat send button. All still in place.

## V31.verified-clean-2 merge: real test suite (609+ tests) + env var regression

A parallel session added genuine pytest coverage across the whole codebase
(`tests/`, 609+ passing) and, in the process, found and fixed 3 real bugs:
- `_normalize_question()` in `smart_quiz_scanner_routes.py`: a short
  `options` list (<4 items) built a partial dict, then an unconditional
  A-D lookup raised `KeyError` - one malformed OCR'd question could 500 an
  entire scan. Fixed to always build the full 4-key dict first.
- `send_notification()` in `app.py`: used `firestore.client()` without
  importing `firestore` - broadcasting to "all" or cleaning up invalid FCM
  tokens always failed with a misleading 502.
- `ai_study_plan()` in `app.py`: `days` was parsed with `int(...)` *before*
  the `try:` block meant to catch exactly that conversion failing - a
  non-numeric `days` value crashed with a 500 instead of a clean 400.

Also added: account suspension (`POST /api/admin/users/<uid>/suspend`,
enforced in `_require_user_bearer` so it covers every route in the app,
fails open on any Firestore error).

- **Regression found on merge:** this export's `render.yaml` was missing
  4 env vars I'd added earlier in this session -
  `CLOUDINARY_UPLOAD_PRESET`, `FCM_VAPID_KEY`, `TELEBIRR_WEBHOOK_SECRET`,
  `CBE_WEBHOOK_SECRET` - confirming the export-drops-things pattern the
  other session's own handoff notes flagged for `tests/`. Re-added.
- **New bug found in this pass:** the new Suspend button
  (Student Directory) correctly uses a `data-attribute` +
  `addEventListener` pattern, safe against names containing an apostrophe
  or quote. The **Delete** button on Teacher Roster still used the older
  `onclick="deleteUserAccount('\${escapeHtml(x.name)}...)"` inline pattern -
  `escapeHtml` HTML-escapes a quote character, but the browser decodes
  that entity back to a literal `'` before running the onclick handler as
  JS, so a teacher name containing an apostrophe would silently break the
  button with a JS syntax error - no error message, just "nothing
  happens" on click. Converted to the same safe `data-attribute` pattern
  used elsewhere. Checked all 16 other inline-onclick call sites across
  `admin.js`/`student.js`; every other one only interpolates
  machine-generated IDs (never free-text names), so they don't share this
  risk.
