# Handoff: BMT-V31 test coverage & cleanup — continuation brief

## Context
This codebase (BMT-V31, ~14,000 lines across 67 Python files) had very
uneven pytest coverage: only 4 telegram-related route files had real tests
(49 tests total). The rest — including money/scoring-critical code like
award finalization and challenge grading — had zero pytest coverage.

## What has already been done (do not redo)
Files already covered with new, real pytest tests (all included in the zip
you're being given):
- `tests/test_award_ledger_routes.py` + `test_award_ledger_finalize.py`
  (`award_ledger_routes.py`)
- `tests/test_teacher_admin_routes.py`, `test_teacher_assignment_grading.py`,
  `test_teacher_apply_courses_exams.py` (`teacher_routes.py`)
- `tests/test_parent_access_control.py` (`parent_routes.py`)
- `tests/test_library_routes.py` (`library_routes.py`)
- `tests/test_learning_challenge_start.py`, `_submit.py`,
  `_question_bank.py`, `_leaderboard.py`, `_practice.py`
  (`learning_challenge_routes.py` — this one is now fully covered)

Also already done:
- `tests/fakes.py` was extended with Firestore feature support that didn't
  exist before: `FakeTransaction` (+ `firestore.transactional` stub),
  `FakeBatch`, subcollections (`FakeDocRef.collection()`), and
  `FakeQuery.order_by()`. Several of the files below will need these — check
  `fakes.py` before assuming a feature is missing.
- `scripts/gcs_storage_regression_test.py` had two genuinely stale checks
  (referencing a deleted `gcs_storage.py` module) fixed to check
  `cloudinary_storage.py` instead.
- `docs/history/INDEX.md` was added (a title/version index over the 158
  markdown files in `docs/history/` — pure documentation, no code touched).

## IMPORTANT — verify my work first, don't just trust it
Every test in the files above was **not run under real pytest**. The sandbox
I worked in has no network access and pytest is not installed, so I verified
each assertion by manually importing the route module and driving it with
the same fake Firestore harness pytest would use, printing PASS/FAIL for
every assertion by hand. This is strong evidence the logic is correct, but
it is not a substitute for an actual pytest run (test discovery, fixture
injection, etc. were never exercised).

**Your first step should be:**
```
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
```
If anything fails, that's either a real bug in my test or a real bug the
test caught — investigate before trusting either the app code or my tests
blindly.

## What's still untested (suggested priority order)
1. `admin_features_routes.py` (822 lines) — untested
2. `smart_quiz_scanner_routes.py` (531 lines) — untested, uses Firestore
   transactions (the fake now supports this)
3. Payment-related code in `app.py` (Chapa integration, webhook handling) —
   high-value but see the blocker below
4. Telegram bot routes beyond the 4 already-tested files
5. Remaining `app.py` inline routes generally

## A real blocker you may hit: app.py cannot be imported in a sandbox
`app.py` imports `flask_cors`, which is correctly listed in
`requirements.txt` (`Flask-Cors==4.0.1`) but was NOT installed in my sandbox,
and I had no network access to install it. This meant I could **not** import
or test `app.py` at all, and could not attempt any structural refactor of it
(e.g. splitting it into Blueprints) because I had no way to verify the app
still starts or that routes are unchanged after an edit.

If your environment has network access or the dependency pre-installed,
this blocker won't apply to you — you should be able to import and test
`app.py` normally. If you hit the same missing-dependency issue, don't
attempt to edit `app.py`'s structure blind; get the dependency installed
first, or limit yourself to additive, easily-revertable changes.

## Test-writing pattern to follow (for consistency)
Look at any of the `tests/test_*.py` files listed above for the pattern:
- `tests/conftest.py` provides a `fake_db` fixture (a `FakeFirestore`
  instance) and a `make_app(register_fn, *args)` helper that builds a
  minimal Flask app from just the one route module being tested — no need
  to import `app.py` at all for testing individual route files.
- Seed only what each test needs via `fake_db.seed(collection, doc_id, dict)`.
- Assert on both the HTTP response AND the resulting Firestore state via
  `fake_db.dump(collection)`.
- Prioritize tests that pin down authorization boundaries (who can access
  whose data), server-authoritative state transitions (status fields that
  must never be settable by the client), and idempotency/replay safety on
  anything using a transaction — these are where real bugs tend to hide in
  this codebase, based on what I found.

## Addendum — smart_quiz_scanner_routes.py now covered, and the app.py blocker is gone

### The app.py blocker no longer applies here
This sandbox now has outbound network access to PyPI. `pip install
flask_cors firebase_admin pytest PyMuPDF cloudinary` (plus a clean venv to
avoid a `PyJWT` conflict with a Debian-packaged version already on the
system Python) installed everything cleanly, and **`app.py` now imports
without error**. Real `pytest` was used for this and all prior work,
replacing the earlier custom pytest-compatible runner — no behavior
difference, just no longer homegrown. If a future session hits the old
missing-network/missing-dependency blocker again, that's an environment
regression, not a fundamental limitation — try `pip install` again first.

### New coverage: `smart_quiz_scanner_routes.py` (531 lines, previously
untested) — `tests/test_smart_quiz_scanner.py`, 55 tests, all passing
- Admin/approved-teacher gating on scan upload; unapproved teachers and
  plain students are rejected.
- Upload validation: missing file, unsupported content-type, >10MB, empty
  file.
- The Library-location metadata chain (`collection`/`bookId`/`chapterId`/
  `subchapterId`): each dependency is validated (chapter needs a book,
  subchapter needs a chapter) and each referenced doc's existence is
  checked all the way down the subcollection path.
- Gemini OCR extraction: missing API key, upstream rejection, malformed
  JSON, non-list `questions`, and that a single malformed *extracted*
  question is silently dropped rather than failing the whole scan.
  Provenance fields (`sourceRef`) are confirmed server-overwritten even
  when the model/client supplies its own.
- The PDF branch (text-PDF vs scanned/image-PDF path selection) with
  `_text_pdf`/`_pdf_pages` monkeypatched at the module level — no real PDF
  parsing needed to test the route's own branching.
- `scanner_get`: ownership/admin access, and that a stored source file's
  signed URL is freshly re-minted on every read rather than reusing a
  possibly-expired one.
- `scanner_review`: immutability once `IMPORTED`, and the deliberate
  asymmetry with extraction — one malformed question here fails the whole
  save (400) rather than being silently dropped, since this is an explicit
  human edit.
- `scanner_import` (the transactional endpoint): every guard (not
  reviewed yet, no questions, missing correct answers, invalid class for
  the classwork destination, unknown destination silently defaulting to
  classwork), successful classwork imports (per-question quiz docs with
  the right className/createdBy/Library-metadata fields, and per-question
  `learningObjective` falling back to scan-level metadata), successful
  questionbank imports (created as `status: draft` pending a human review
  pass before reaching Practice/Challenge/classwork/Telegram), and —
  highest priority, mirroring this module's own comment about closing a
  duplicate-import race — repeated import calls against the same scan
  (tested up to 5 in a row) always settle on the exact same quiz set, never
  duplicating documents.

### One real bug found and fixed (in your code, not just my tests)
`_normalize_question()` in `smart_quiz_scanner_routes.py`: when the OCR
model (or a scanned/reviewed submission) returns `options` as a **list with
fewer than 4 items**, the old code built a partial `{"A": ..., "B": ...}`
dict and then unconditionally read `options["C"]`/`options["D"]`, raising
an uncaught `KeyError`. In practice this turned one malformed question
into a full 500 for the *entire* scan-extraction or review request,
instead of that one question being safely dropped (extraction) or
rejected with a clean 400 (review) as the code clearly intends everywhere
else. Fixed by always building the full A-D dict with missing letters
defaulted to `""` before the option list is applied. Covered by
`test_malformed_questions_are_silently_dropped_not_errored` and
`test_one_bad_question_fails_the_whole_save`, both of which failed against
the old code and pass now.

### Infrastructure added
- `tests/fakes.py`: `FakeGeminiAPI`, a `requests.post` stand-in for
  `generativelanguage.googleapis.com` shaped like the real
  `generateContent` response envelope (`candidates[].content.parts[].text`
  holding a JSON string) — supports scripting specific extracted
  questions, arbitrary/malformed response text, and upstream-rejection
  failures.
- `tests/conftest.py`: a `gemini_api` fixture mirroring the existing
  `telegram_api`/`cloudinary_api` ones, for tests that just need Gemini to
  return one well-formed default question without scripting it by hand.

## Still untested (updated priority order)
1. Payment-related code in `app.py` (Chapa integration, webhook handling).
2. Telegram bot routes beyond the already-tested files.
3. Remaining `app.py` inline routes generally.
`admin_features_routes.py` and `smart_quiz_scanner_routes.py` are now both
fully covered.

## Addendum 2 — app.py is now covered (payments, telegram admin, auth, storage, exams, notifications, content/config, AI gateway)

This closes out all three items above. `app.py` (the monolithic Flask app,
2,777 lines, previously untested beyond what the sub-module route files
cover) now has **287 new tests across 7 files**, all passing, in addition
to everything from Addendum 1. Full suite: **601 passed**.

Because `app.py` isn't a `register_x_routes(app, ...)` module like the
others, these tests use a new `app_module` fixture (`tests/conftest.py`)
that imports `app.py` directly and monkeypatches its module-level
`_require_user_bearer`/`_require_admin_bearer` — this only affects app.py's
own inline routes, not the sub-modules it also registers (they captured
their own auth functions as arguments at import time).

### `tests/test_app_payments.py` (54 tests) — the actual highest-priority item
Chapa initialize/callback/webhook (self-pay and parent-pays-for-child,
amount/currency cross-checks, signature verification), the
provider-agnostic submit/parent-submit/approve/reject endpoints, and the
generic telebirr/cbe gateway webhook. Repeated-call and repeated-webhook-
delivery idempotency are both covered (an approved/verified payment never
grants a second entitlement stack; a renewal correctly extends from the
remaining time on an unexpired entitlement rather than restarting from
"now").

### `tests/test_app_telegram_admin.py` (36 tests)
The admin-facing Telegram bot control panel (status + set-webhook) for
all three bots — academic/main, moderator, publisher — parameterized
across all three since they share the same two helper functions. This was
the actual content of "Telegram bot routes beyond the already-tested
files": the routes that handle each bot's own inbound traffic already had
dedicated test files; these admin/config routes are inline in app.py and
had none.

### `tests/test_app_auth.py` (26), `test_app_storage.py` (17), `test_app_exams.py` (27), `test_app_notifications.py` (19), `test_app_content_and_config.py` (39), `test_app_ai.py` (16)
- **Auth**: forgot-password (including that account enumeration is
  impossible — an unknown email gets the same generic success response as
  a real one), phone+PIN register/login, and the admin-claims token
  upgrade. Needed a new `fake_firebase_auth` fixture (`FakeFirebaseAuth` in
  `tests/fakes.py`) since these routes call `firebase_admin.auth` directly
  rather than only going through the bearer-token check.
- **Storage**: upload gating, and `_can_access_gcs_book`'s access-control
  chain for `/api/storage/document-url` — a storage path alone is never
  sufficient; it has to trace back to a Firestore book record the caller
  is actually entitled to (their class, the book they uploaded, or admin).
- **Exams**: available/start/save/grade/history/result. Confirms the
  answer key never reaches the browser before grading, re-grading an
  already-submitted attempt is idempotent (returns the stored score
  regardless of what a second payload claims), and — the one that would
  otherwise be gameable — a late submission past the deadline is scored
  off the server's last autosave, not the browser's late payload.
- **Notifications**: list/read/read-all, admin announcements (with and
  without FCM push, including that opted-out users are skipped), and
  `/api/notifications/send` (including invalid-token cleanup). Needed a
  new `fake_messaging` fixture (`FakeMessaging` in `tests/fakes.py`) for
  `firebase_admin.messaging`.
- **Content/config**: admin preview tokens (mint/verify/tamper/expiry),
  the config diagnostic's two independent auth paths (admin bearer OR a
  shared diagnostic key), library/quiz/video listing, and video listing's
  entitlement-gating logic (paid content requires active premium or an
  eligible active free trial, checked against real expiry timestamps, not
  just a boolean flag) — plus a smoke test over the static/health pages.
- **AI gateway**: `/api/ai/translate` (direct Gemini call, same shape as
  the scanner) and `/api/ai/tutor` + `/api/ai/homework-coach` (via
  `ai_tutor_service`'s shared quota-gated pattern, mocked at that layer
  rather than re-testing Gemini). `/api/ai/homework-image`,
  `/api/ai/study-plan`, and `/api/ai/practice-quiz` share this same
  delegation pattern and were **not** individually covered this pass —
  see "Still untested" below.

### Two more real bugs found and fixed (in your code)
1. **`_normalize_question()` in `smart_quiz_scanner_routes.py`** — already
   documented above; repeating here only because it also affects any
   caller with a short `options` list, not just the scanner endpoints.
2. **`send_notification()` in `app.py`** (`/api/notifications/send`): used
   `firestore.client()` in two places (the `target: "all"` token lookup,
   and invalid-token cleanup) but only imported `firebase_admin` and
   `messaging` at the top of the function — `firestore` was never
   imported. Both call sites raised `NameError`, caught by the generic
   exception handler and surfaced as a misleading `FCM_SERVER_ERROR` 502,
   so broadcasting to "all" or any run that hit an unregistered device
   token silently never worked. Fixed by adding `firestore` to the
   existing `from firebase_admin import ...` line. Caught by
   `test_target_all_pulls_tokens_from_firestore` and
   `test_invalid_tokens_are_removed_and_reported`, both of which failed
   against the old code.

### Infrastructure added this pass (all in `tests/fakes.py` + `tests/conftest.py`)
- `FakeChapaAPI` / `chapa_api` fixture — app.py's Chapa helper calls
  `requests.request` (not `requests.post` like everything else), so this
  needed its own patch target. Scripts both the initialize and verify
  response shapes.
- `FakeTelegramBotAdminAPI` — patches both `requests.get` and
  `requests.post` for the admin bot control panel's `getMe`/
  `getWebhookInfo`/`setWebhook` calls.
- `FakeFirebaseAuth` / `fake_firebase_auth` fixture — a minimal
  `firebase_admin.auth` stand-in (`get_user_by_phone_number`,
  `create_user`, `create_custom_token`) for the phone+PIN auth routes.
- `FakeMessaging` / `fake_messaging` fixture — a minimal
  `firebase_admin.messaging` stand-in (`Message`, `Notification`,
  `send_each`, with scriptable per-token success/failure) for FCM push.
- `SERVER_TIMESTAMP` sentinel and a `Query` stub (`DESCENDING`/
  `ASCENDING`) added to the fake `firebase_admin.firestore` module —
  needed once app.py's own code was exercised (`firestore.SERVER_TIMESTAMP`
  and `firestore.Query.DESCENDING` are both used there and hadn't come up
  in the sub-module tests before now). `FakeDocRef.set()`/`.update()` now
  resolve the sentinel to a real datetime instead of leaving it as an
  opaque object, so any assertion on the written timestamp works.
- `_reset_app_rate_limits` **autouse** fixture — app.py's rate limiter is a
  module-level dict keyed by client IP + path that's never reset. The
  Flask test client always presents the same IP, so without this reset
  the limiter's own 30-requests/60s default would start rejecting later
  tests in any file that calls the same endpoint more than ~30 times
  across the whole run. Only touches state once `app` has actually been
  imported, so it's a no-op for every other test file.
- `app_module` fixture — see above.

### An unrelated environment fix worth knowing about
This sandbox turned out to have outbound network access to PyPI after
all. Real `pytest`, `flask_cors`, `firebase_admin`, `PyMuPDF`, and
`cloudinary` are now installed (in a venv at the repo root to dodge a
`PyJWT` version conflict with a Debian-packaged copy on the system
Python), and **`app.py` now imports without error** — the long-standing
"no network / flask_cors not installed" blocker mentioned in every
previous handoff no longer applies in this environment. If a future
session hits that blocker again, try `pip install` before assuming it's a
hard limitation.

## Still untested (final priority order after this pass)
1. `/api/ai/homework-image`, `/api/ai/study-plan`, `/api/ai/practice-quiz`
   — same `ai_tutor_service` delegation pattern as `/api/ai/tutor` and
   `/api/ai/homework-coach` (both now covered), just not individually
   exercised yet.
2. `ai_tutor_service.py` itself (quota accounting internals, material
   retrieval ranking, the actual Gemini prompt construction) — the app.py
   tests mock this module as a black box rather than testing it directly.
3. `smart_quiz_scanner_routes.py`'s Telegram bot modules'
   (`telegram_challenge_routes.py` etc.) own webhook handlers were already
   covered before this pass and are unaffected by it.
4. Edge cases within already-covered routes that a deeper pass would
   still find (e.g. `_normalize_phone`'s exact accepted formats, every
   `PAYMENT_PLANS` price point) — the tests added focus on
   branch/guard/idempotency coverage rather than exhaustive input-space
   coverage.

At this point every route file and every inline route group in `app.py`
has at least baseline coverage; what remains is depth in a few pockets
(AI content-generation endpoints, `ai_tutor_service.py` internals) rather
than untouched surface area.

## Addendum 3 — the AI content-generation gap is closed

This closes out the two items Addendum 2 left open. **68 new tests**
across 2 files; full suite is now **656 passed**.

### `tests/test_ai_tutor_service.py` (33 tests) — direct unit tests, not black-box mocks
- `check_and_record_quota`: per-user daily accounting (accumulates
  correctly, isolated per uid), premium vs free limits, and confirms it
  **fails open** on a Firestore error (returns allowed=True rather than
  locking every student out if quota tracking itself breaks — worth
  knowing this is deliberate, not an oversight, if you're ever debugging
  "why did quota let this through").
- `retrieve_material_context`: grade/subject filtering, inactive/empty
  chunks excluded, the keyword-overlap fallback ranking, and — the one
  that actually proves the semantic branch does something — a case where
  the embedding-based ranking picks a *different* top result than keyword
  overlap would have, confirming the semantic path really short-circuits
  the fallback rather than running unused.
- `ask_gemini`: both response shapes it defensively parses (`output_text`
  and the newer `steps` timeline from Google's May-2026 breaking change),
  retry-on-429/5xx with eventual success or exhaustion, that a 4xx client
  error is *not* retried (fails on the first attempt), and that materials
  actually make it into the system prompt sent to Gemini.
- `generate_practice_quiz`: valid-question parsing, the regex fallback for
  JSON wrapped in prose, per-question validation (wrong option count, bad
  answerIndex, empty question text all silently filtered rather than
  crashing the batch), and count clamping to 10.

### `tests/test_app_ai.py` — extended with 22 new tests (was 16, now 38)
- `/api/ai/homework-image`: upload validation (missing/unsupported-mime/
  empty), quota gating, Gemini's own 429 vs other-failure distinction, and
  a happy-path assertion that the uploaded image bytes actually reach
  Gemini as inline base64 data (not just that *some* request went out).
- `/api/ai/study-plan`: missing goal/weakTopics, quota gating, days
  clamped to 30, and — see the bug below — invalid `days` input.
- `/api/ai/practice-quiz`: missing topic, quota gating, the 404 when no
  matching BMT material exists for the topic, and count clamping to 10.

### A third real bug found and fixed (in your code)
**`ai_study_plan()` in `app.py`** (`/api/ai/study-plan`): computed
`days = max(1, min(int(payload.get('days', 7) or 7), 30))` **before** the
`try:` block that exists specifically to catch `(ValueError, TypeError)`
and turn it into a clean 400 ("Invalid study-plan duration."). Since the
int() conversion ran outside that try block, a non-numeric `days` value
(e.g. a string, or valid JSON that isn't a number) crashed with an
unhandled 500 instead. Fixed by moving the `days` computation inside the
try block, ahead of everything else in it. Caught by
`test_invalid_days_type_returns_400`, which failed against the old code.

### Infrastructure added this pass
- `FakeHttpResponse` (`tests/fakes.py`) gained `.headers` and `.text`
  attributes (both previously absent) — needed once `ask_gemini`'s
  429-retry path (which reads `response.headers.get("Retry-After")`) was
  actually exercised for the first time.
- No new fixtures were needed beyond that — `ai_tutor_service.py`'s tests
  patch `requests.post` directly per-test (its Gemini Interactions API
  response shape — `output_text`/`steps` — is different enough from the
  `candidates`-based `generateContent` shape used everywhere else that a
  shared fixture wouldn't have been reused much; see the `_interactions_response()`
  helper at the top of the test file if a future session wants to
  promote it into `tests/fakes.py`).

## Still untested (final, after all three addenda)
1. Edge cases within already-covered routes that a deeper pass would
   still find (e.g. `_normalize_phone`'s exact accepted formats, every
   `PAYMENT_PLANS` price point, `embedding_service.py`'s own HTTP-call
   internals beyond what `retrieve_material_context`'s semantic-path test
   exercises through it).
2. Anything genuinely new added to the codebase after this point.

Every route file, every inline route group in `app.py`, and
`ai_tutor_service.py` now has real test coverage — this was the last
open item from the original handoff's priority list.

## Addendum 4 — verification of another session's follow-up work, one test-infra bug fixed, and an important gap found in this project's file history

A later session picked up the three items Addendum 3 left open
(`embedding_service.py`, `_normalize_phone()` edge cases, per-plan
`PAYMENT_PLANS` coverage) and also built an entirely new feature:
account suspension (`POST /api/admin/users/<uid>/suspend` in
`student_profile_routes.py`, enforced app-wide via `_check_not_suspended`
inside `app.py`'s `_require_user_bearer`). This addendum is this
session's audit of that work — verifying it under real pytest rather
than trusting it, per this document's own standing instruction.

### The new work is genuinely excellent
- `tests/test_embedding_service.py` (16 tests): request shape, batching
  (`MAX_BATCH` split across requests), every error path
  (upstream-error-with/without-message, empty embedding values, an
  incomplete batch), and `cosine_similarity`'s degenerate-input handling
  (empty/`None`/mismatched-length vectors, zero vectors) — all exactly
  what Addendum 3 asked for, plus extra cases (blank/`None` texts coerced
  to empty strings rather than dropped, which matters for keeping the
  embeddings list aligned with the source chunk list).
- `tests/test_normalize_phone.py` (16 tests): every prefix-stripping
  branch for both the 7-series and 9-series local numbers, punctuation/
  whitespace stripping, and a thorough rejected-shapes list — including
  catching that a bare `int` input is accepted (since the function
  stringifies before matching) rather than assuming it would be rejected.
- `tests/test_payment_plans.py`: parametrizes over
  `app_module.PAYMENT_PLANS.items()` directly rather than hardcoding plan
  names, so a new plan added to `app.py` in the future is automatically
  covered by these tests with zero edits — a nice piece of future-proofing
  worth following as a pattern elsewhere.
- `tests/test_app_suspension.py` (new feature, 15 tests): correctly
  identified and worked around a real gotcha — `app_module`'s
  monkeypatched `_require_admin_bearer` only reaches `app.py`'s own
  inline routes, not routes registered via `register_x_routes(app, ...)`
  calls (like `student_profile_routes.py`, where the suspend endpoint
  actually lives), because those modules captured the auth function as a
  plain argument at import time. The admin-toggle tests correctly fall
  back to the `make_app()` pattern instead. The enforcement-path tests
  also correctly exercise the *real* `_require_user_bearer()` (not a
  monkeypatched stand-in) via a new `fake_firebase_auth.script_id_token()`
  helper, since that's the one function this feature's logic actually
  lives inside. Also confirms the suspension check fails open on a
  Firestore error (consistent with the same fail-open design already
  documented for AI quota checking in Addendum 3) and that a revoked
  token (401, generic) stays distinguishable from a suspended account
  (403, with the admin's reason surfaced) rather than the two failure
  modes bleeding into each other.

### One test-infrastructure bug found and fixed (not a bug in your app code)
`tests/conftest.py`'s `fake_firebase_auth` fixture builds a fake
`firebase_admin.auth` module but never wired up
`revoke_refresh_tokens` — only `get_user_by_phone_number`, `create_user`,
`create_custom_token`, and `verify_id_token` were attached, even though
`FakeFirebaseAuth` in `tests/fakes.py` already implements
`revoke_refresh_tokens`/`revoked_uids` (used by the new suspension
tests). The suspend route calls
`firebase_auth.revoke_refresh_tokens(uid)` inside a deliberate
best-effort `try/except: pass` (it's documented as best-effort — the
Firestore `isSuspended` flag is the real, authoritative gate), so the
missing attribute raised `AttributeError`, was silently swallowed exactly
as designed, and `test_suspending_sets_flag_reason_and_revokes_tokens`
failed because `revoked_uids` stayed empty. **This is a test-infra gap,
not an app.py bug** — the suspend endpoint's actual behavior (flag,
reason, audit fields) was all correct; only the fixture's ability to
*observe* the best-effort token revocation was broken. Fixed with a
one-line addition to the fixture:
```python
mod.revoke_refresh_tokens = fake.revoke_refresh_tokens
```
Full suite after the fix: **467 passed** (was 466 passed, 1 failed).

### An important gap: several previously-documented test files are missing from this zip
This document's own history (see the very first section at the top of
this file, and Addenda 1-3) references test files covering
`admin_features_routes.py`, `award_ledger_routes.py`, `teacher_routes.py`,
`parent_routes.py`, `library_routes.py`, and `learning_challenge_routes.py`
as already written and passing:
- `tests/test_admin_settings_catalog.py`, `test_admin_chat_moderation.py`,
  `test_community_chat.py` (→ `admin_features_routes.py`)
- `tests/test_award_ledger_routes.py`, `test_award_ledger_finalize.py`
  (→ `award_ledger_routes.py`)
- `tests/test_teacher_admin_routes.py`, `test_teacher_assignment_grading.py`,
  `test_teacher_apply_courses_exams.py` (→ `teacher_routes.py`)
- `tests/test_parent_access_control.py` (→ `parent_routes.py`)
- `tests/test_library_routes.py` (→ `library_routes.py`)
- `tests/test_learning_challenge_start.py` + `_submit.py`,
  `_question_bank.py`, `_leaderboard.py`, `_practice.py`
  (→ `learning_challenge_routes.py`)

**None of these files are present in the `tests/` directory of the zip
this session received.** The source files they're supposed to cover
(`admin_features_routes.py`, `award_ledger_routes.py`, `teacher_routes.py`,
`parent_routes.py`, `library_routes.py`, `learning_challenge_routes.py`)
are all still present and, per this document's own history, were
previously fully tested — so right now, by this zip's actual contents,
they have **zero** test coverage, contradicting what this same document
says a few paragraphs above.

This has now happened at least twice across different sessions/zip
exports (a different, smaller upload this session's predecessor worked
from was also missing files this document referenced as existing) — the
pattern suggests something in however this project gets zipped/exported
between sessions is dropping files, rather than any one AI session
deleting them. **Before assuming this work is actually lost, check the
project's real source of truth (local disk or GitHub) for these test
files** — they may simply not have made it into this particular zip.
If they truly are gone, they would need to be rewritten from scratch (the
descriptions above at least document what each one covered, as a
starting point).

This session did not attempt to rewrite any of the six modules above -
that's a large enough undertaking (and important enough to get right
rather than duplicate work that may already exist elsewhere) that it
belongs in its own follow-up pass once the missing-files question is
resolved, not folded into this verification pass.

## Addendum 5 — the missing files were found, not lost: merged, 609 passing

The person re-uploaded an earlier zip (`BMT-V31_updated-improved.zip`)
that turned out to contain exactly the test files Addendum 4 flagged as
missing. This confirms the suspicion in Addendum 4: nothing was deleted —
different zip exports of this project have simply been carrying
different, non-overlapping snapshots of `tests/`, and no single export
seen so far has had the full set. **Lesson for whoever manages these
exports going forward: zip the entire `tests/` directory, don't rely on
any one export being authoritative.**

Before merging, this session verified it was actually safe to:
- The five source modules these 12 files cover
  (`award_ledger_routes.py`, `teacher_routes.py`, `parent_routes.py`,
  `library_routes.py`, `learning_challenge_routes.py`) are **byte-identical**
  between the old zip and this project's current `app.py`-generation
  code — a `diff` on all five came back empty, so the old tests are
  testing the exact code that's here now, not a stale version of it.
- The old zip's `tests/fakes.py` and `tests/conftest.py` (which differ
  substantially from this project's current versions — 361 and 131 diff
  lines) were confirmed to be a **strict subset**: every class/function
  defined in the old `fakes.py` and every fixture in the old `conftest.py`
  already exists in the current versions (built up across Addenda 1-4).
  So the newer `fakes.py`/`conftest.py` were kept as-is; only the 12 test
  files themselves were copied over.

The 12 files merged in (all now in `tests/`):
`test_award_ledger_finalize.py`, `test_award_ledger_routes.py`,
`test_teacher_admin_routes.py`, `test_teacher_apply_courses_exams.py`,
`test_teacher_assignment_grading.py`, `test_parent_access_control.py`,
`test_library_routes.py`, `test_learning_challenge_start.py`,
`test_learning_challenge_submit.py`,
`test_learning_challenge_question_bank.py`,
`test_learning_challenge_leaderboard.py`,
`test_learning_challenge_practice.py` — **142 tests, all passing**
against the current codebase with zero changes needed to either the
tests or the app code.

**Full suite after the merge: 609 passed.** `app.py` still imports
cleanly.

### Still genuinely missing
`tests/test_admin_settings_catalog.py`, `test_admin_chat_moderation.py`,
and `test_community_chat.py` (covering `admin_features_routes.py`) were
**not** in this re-upload either, despite this same document's very first
addendum describing them as written and passing. `admin_features_routes.py`
itself is present and unchanged, so if these three files turn up in yet
another old export, the same verify-then-merge approach used here should
apply directly. If they don't turn up anywhere, they're the one module
that would genuinely need to be rewritten from scratch — see this
document's Addendum 1 for what they covered, as a spec to work from.
