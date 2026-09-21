# Changelog (recent work)

Older version-by-version notes from past development sessions were moved to
`docs/history/` — they're historical record, not required reading. This file
tracks what actually changed recently, in plain terms.

## V31.108 Follow-up: deployment readiness (Gemini, .gitignore, render.yaml, docs)
- **Gemini:** the Library/AI translation endpoint defaulted to the retired
  `gemini-1.5-flash`; it now defaults to `gemini-3.6-flash` like every other
  AI route. Removed the deprecated `temperature` sampling parameter from the
  three raw `generateContent` calls (`app.py` x2, `learning_challenge_routes.py`);
  Google's migration guide for Gemini 3.x says to strip it, and it can be
  rejected with HTTP 400.
- **`.gitignore` added** (Python caches, `.env`, service-account JSON files).
- **`render.yaml`:** declared `ENABLE_FCM_PUSH` (read by the code, was missing).
- **`DEPLOYMENT.md`:** current Gemini model ID, Chapa webhook URL, Cloudinary
  unsigned preset, self-generated secrets (`ADMIN_PREVIEW_SECRET`,
  `BMT_CERT_VERIFY_SECRET`, ...), push-notification setup and the Firebase
  Console one-time steps (providers, Authorized domains, first admin).

## V31.108 Follow-up: Teacher header + Student hero title (Tigrinya)
- **Teacher dashboard:** removed the post-login "Forgot Password?" header
  button (the sign-in one stays).
- **Student dashboard hero (Tigrinya):** "Learn at your own pace." now reads
  ብዝደለኹምዎ ፍጥነትን ብቕዓትን ተምሃሩ! as one sentence. English and Amharic
  unchanged (Amharic still uses the old word-by-word dictionary entries).

## V31.108 Follow-up: UI cleanup (admin, footers, index title, warm background)
- **Admin dashboard:** removed the post-login "Forgot Password?" header button
  (a signed-in admin does not need it). The one on the sign-in screen stays.
  The Teacher header still has the same button; not touched.
- **Duplicate contact footers removed** ("Bright Mind Tutor · Ethiopia", phone,
  email) at the bottom of the Admin, Teacher and Parent dashboards. The same
  contact details remain on the landing page and in Student > Support.
- **Landing page title translation:** the hero title is now one whole sentence
  per language instead of word-by-word swapping, which broke the grammar.
  Tigrinya: ምስ ቤት ትምህርቲ ብሩህ ኣእምሮ ብውሕሉል ኣገባብ ኣስተምህሩን ተምሃሩን!
  Amharic: ከብሩህ አእምሮ ትምህርት ቤት በዘመነ መንገድ አስተምሩ፣ ተማሩ!
  (Implemented in `templates/index.html` with per-language spans.)
- **Light theme background is now light brown (#F6ECDA)** instead of light
  grey/blue: `--bg` and the page gradient in `static/style.css`, plus
  `templates/index.html` and `templates/auth.html`. Portal pages get the same
  colour under their soft gradients. Dark theme unchanged.

## V31.108 Follow-up: language selector on landing, login and admin pages
The English / አማርኛ / ትግርኛ selector (`i18n.js`, `<div data-lang-switcher>`)
existed only in the Student, Teacher and Parent dashboards, which are shown
after login. The landing page, the login page and the Admin dashboard had no
selector, so it looked missing when running locally. Added it to
`templates/index.html`, `templates/auth.html` and `templates/admin.html`
(selector + `i18n.js` script tag only; no logic changed). Choice is shared
across pages via `localStorage["bmt_lang"]`. Note: the existing dictionary
covers most of the login page but only part of the Admin dashboard, so untranslated
Admin text stays in English until strings are added with `BMT_I18N.add(...)`.

## V31.108 Follow-up (correction): test-suite numbers were a runner artifact
The previous entry below quoted "816/831" and later "685 passed / 66 failed /
13 skipped". Both figures came from the stand-in runner
`scripts/v31_108_unit_structure_test_runner.py` and were not accurate. That
runner cannot resolve fixtures defined inside test modules, has no
`parametrize`, and the sandbox lacked `cloudinary`. Re-running the suite under
real pytest (`pip install -r requirements.txt -r requirements-dev.txt`) gives
**847 passed, 0 failed, 0 skipped** with no code changes. The ~66 "failures"
were all runner limitations, not product bugs.

- Run tests with `python -m pytest tests` (not bare `pytest`): the
  `scripts/*_test.py` files raise `SystemExit` at import and break collection.
- The custom runner is kept only as a fallback for environments where pytest
  cannot be installed. Still tested against fake Firestore/Auth only, not
  real Firebase.

## V31.108 Follow-up: 8 known test failures, PIN lockout, phone-pin/reset (incremental, version unchanged)
Addresses the open items left by the previous audit-fixes pass. Not verified
against real Firebase — tested against the fake Firestore/Firebase Auth used
throughout `tests/`.

- **PIN lockout now actually enforced.** `app.py` `phone_pin_login`: the
  transaction wrote `lockedUntil` after 10 failed attempts but never read it
  back, so the lock never engaged. It now checks `lockedUntil` first, inside
  the same transaction, and returns 429 while locked — before the PIN is even
  checked. New tests in `tests/test_app_auth.py::TestPhonePinLogin`.
- **`/api/auth/phone-pin/reset` now requires `firebase.sign_in_provider ==
  'phone'`**, not just a `phone_number` claim, so the reset can only be used
  with a token that came from a fresh Phone Auth OTP verification. New tests
  in `tests/test_app_auth.py::TestPhonePinReset`.
- **AI quota tests updated to match the code's fail-closed behavior**
  (`tests/test_ai_tutor_service.py`, 3 tests). The code already fails closed
  (raises rather than granting unlimited AI use) when there's no `db_getter`
  or when the Firestore transaction breaks; the tests still asserted the old
  fail-open behavior. Decision: fail-closed is correct — an infrastructure
  problem should not turn into unlimited paid AI usage.
- **3 stale `test_app_ai.py` mocks fixed**: `retrieve_material_context` now
  takes a `uid` kwarg (added for V31.108 materials targeting); the tests'
  mock lambdas didn't accept it and crashed instead of exercising the route.
- **2 `TestLibraryList` tests fixed** (`test_app_content_and_config.py`):
  they asserted on `/api/library` behavior without seeding the calling
  user's Firestore doc with a `class`, so the (correct) student-class filter
  returned nothing. Seeded `class: "7"` to match what the test expects.
- **New: documented, not fixed** — `library_routes.py`'s `_can_read_book`
  only allows admins and the *owning* teacher to open a book's chapters, but
  `/api/library`'s list shows *every* approved teacher every class's books.
  An approved teacher can therefore see a book in their list and get 403
  opening it if they didn't upload it. Added
  `tests/test_library_routes.py::test_approved_teacher_cannot_read_another_teachers_book`
  to pin down current behavior. Left unchanged — whether teachers should be
  able to read each other's uploaded books is a product decision, not
  addressed here.
- **Test runner overhaul** (`scripts/v31_108_unit_structure_test_runner.py`):
  the previous version only supported the `fake_db` fixture, so most of the
  suite (anything needing `monkeypatch`, `app_module`, `fake_firebase_auth`,
  etc.) was never actually executing — it silently contributed 0 to both the
  pass and fail counts in every previous session's totals. Rewrote it with a
  real fixture resolver (dependency injection, generator-based setup/
  teardown, `monkeypatch.undo()` now actually runs — the first version never
  called it, which was leaking monkeypatched state across every test that
  ran after it in the same process and could produce false passes/failures
  in a full-suite run) plus `pytest.raises`/`pytest.approx`/`pytest.param`
  stand-ins and stubs for two packages not installed in this sandbox
  (`flask_cors`, so `app.py` can import at all). Only supports what this
  project's tests need, not a general pytest replacement.
- **Full-suite result under the rebuilt runner**: 685 passed, 66 failed, 13
  skipped, up from a runner that could only actually execute a fraction of
  the suite before. The 66 remaining failures are unrelated to the items
  above and mostly runner/sandbox gaps rather than app bugs: fixtures
  defined locally inside a test file rather than in `tests/conftest.py`
  (`client`, `normalize_phone`, several telegram param fixtures — the
  runner only resolves conftest fixtures) and one missing package
  (`cloudinary`, used by `cloudinary_storage.py`). Not investigated further
  in this pass — flagged for a follow-up if useful.

## V31.108 Final audit fixes (incremental, version unchanged)
- `app.py` `phone_pin_login`: `now` was undefined, so every phone+PIN sign-in
  returned HTTP 500. Defined it, and the failed-attempt counter now runs in
  `@firestore.transactional` (the hand-driven transaction was not valid on the
  real SDK). Not verified against real Firestore.
- `app.py` `/api/library`: approved teachers (no student grade on file) got an
  empty Digital Library. Approved teachers are now treated like admins there;
  student grade / Learning Mode / Audience isolation is unchanged.
- Legacy lesson `url` (teacher_routes.py, student_course_routes.py,
  static/student.js, `lesson_blocks.safe_web_link`): javascript:/data:/etc.
  links were accepted, returned to students and put in an href. Now only
  http(s) links are accepted on create/edit, stored bad values are blanked
  before reaching a student, and the student page only links http(s) URLs.
- New tests: `tests/test_v31_108_audit_fixes.py` (12).

## V31.108 Interactive Lesson Builder (incremental, version unchanged)
- Teachers can build a lesson from ordered, reusable blocks inside the existing
  Teacher Dashboard (Courses tab): Text, Formula, Image, Video, PDF/Reading,
  GeoGebra, External Simulation, Practice, Quiz, Assignment. Students read
  them in order in the Distance course viewer (mobile friendly).
- New files: `lesson_blocks.py` (block registry, sanitising, URL/GeoGebra
  validation, grading), `lesson_builder_routes.py` (routes),
  `static/lesson_builder.js`, `tests/test_lesson_builder.py`,
  `docs/INTERACTIVE_LESSON_BUILDER.md`.
- Existing files touched (small, additive): `app.py` (route registration),
  `student_course_routes.py` (`hasBlocks` flag on lesson rows), `static/student.js`
  (open block lessons), `templates/student.html`, `templates/teacher.html`,
  `static/style.css`, `firestore.rules`.
- Blocks live on the existing `lessons` document; quiz/practice answer keys live
  only in the server-only `lessonBlockKeys` collection. Old lessons are unchanged.

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
