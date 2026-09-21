# BMT Dashboard Redesign — Handoff Instructions (v3 — all dashboards converted)

## Status

| File | Status |
|---|---|
| `templates/auth.html` | Done |
| `templates/student.html` | Done (reference implementation) |
| `templates/teacher.html` | Done — real tab-switching, verified (14 tabs, no leaks) |
| `templates/parent.html` | Done — real tab-switching, verified (6 tabs, no leaks) |
| `templates/admin.html` | Done — real tab-switching (12 tabs, no leaks), verified with Playwright at 390x844 + 1280x800 using the real `static/admin.js` against mocked Firebase/API |

Do not re-touch `teacher.html` or `parent.html` — they have been converted and
verified (tag-balance-checked, `node --check`-ed, and screenshot-tested tab by
tab with Playwright). `admin.html` has now been converted as well (see notes below).

## What "done" looks like — copy this pattern exactly

Reference the finished `templates/teacher.html` and `templates/parent.html` as
your primary examples — they are more directly comparable to admin.html
(a logged-in dashboard) than student.html. The recipe:

1. **CSS** — add these three rules near the other `.bmt-*` dashboard rules
   (search for `.bmt-dashboard-nav{display:none !important}` in the file and
   add right after it):
   ```css
   .bmt-tab-panel{display:none}
   .bmt-tab-panel.bmt-tab-active{display:block;animation:bmtAdminTabFadeIn .22s ease both}
   @keyframes bmtAdminTabFadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
   ```
   (Give the keyframe a page-unique name so it doesn't collide if this CSS is
   ever shared across pages.)

2. **The switch function** — replace whatever nav function admin.html
   currently uses (likely `bmtGo(id, btn)` doing `scrollIntoView`, same as
   teacher/parent had) with this, verbatim, placed in the same inline
   `<script>` block where `bmtOpenDrawer`/`bmtCloseDrawer` already live:
   ```js
   window.bmtSwitchTab = function(tab) {
       document.querySelectorAll('.bmt-tab-panel').forEach(function(el){
           el.classList.toggle('bmt-tab-active', el.getAttribute('data-tab') === tab);
       });
       document.querySelectorAll('.bmt-bottom-nav button[data-tab], .bmt-drawer button[data-tab]').forEach(function(btn){
           btn.classList.toggle('active', btn.getAttribute('data-tab') === tab);
       });
       try { localStorage.setItem('bmtAdminActiveTab', tab); } catch(e){}
       var main = document.getElementById('portal'); // use admin.html's actual portal container id
       if (main && main.scrollIntoView) main.scrollIntoView({behavior:'smooth', block:'start'});
       bmtCloseDrawer();
   };
   document.addEventListener('DOMContentLoaded', function(){
       var saved = 'overview';
       try { saved = localStorage.getItem('bmtAdminActiveTab') || 'overview'; } catch(e){}
       if (!document.querySelector('.bmt-tab-panel[data-tab="' + saved + '"]')) saved = 'overview';
       bmtSwitchTab(saved);
   });
   ```
   Use a unique localStorage key per page (`bmtAdminActiveTab`, as above) —
   teacher.html uses `bmtTeacherActiveTab`, parent.html uses
   `bmtParentActiveTab`. Do not reuse another page's key.

3. **Nav buttons** — every bottom-nav and drawer `<button>` gets a `data-tab="x"`
   attribute and `onclick="bmtSwitchTab('x')"`, replacing the old `bmtGo('id', this)`.
   The very first bottom-nav button keeps `class="active"` hardcoded (the
   script fixes real state on load; this is just so it isn't unstyled for a
   flash before JS runs).

4. **Content sections** — every major card/section gets `class="bmt-tab-panel"`
   and `data-tab="x"` added (keep any existing classes/ids — just add these
   two). Multiple cards can share the same `data-tab` value if they belong
   under one nav item (e.g. teacher.html's `exams` tab has 3 stacked cards:
   Exam Results, Mark List, Exam Builder) — `bmtSwitchTab` toggles all
   matching elements via `querySelectorAll`, so this is safe and is the
   established pattern. You do not need sub-tabs unless a section is
   unusually long; flat stacking under one tab is fine and matches
   teacher.html/parent.html.

   Use this exact grouping (already agreed, do not rename these values):
   - Bottom nav: `overview`, `controlcenter`, `students`, `teachers`, `payments`
   - Drawer -> Content: `digitallibrary`, `scanner`, `challenges`, `growthmedia`
   - Drawer -> Connections: `parentlinking`, `telegram`, `community`

## Three real bugs we hit on teacher.html/parent.html — check for these on admin.html too

These are not hypothetical — all three were found and fixed during the
teacher.html/parent.html conversion, so admin.html almost certainly has at
least one of them:

1. **Orphan cards with no nav item.** On teacher.html, 6 cards (Create Course,
   Add Lesson, Lesson Plans, AI Copilot, Exam Results, Mark List) existed in
   the HTML but were never wired into the old `bmtGo` nav at all — they simply
   sat in the page with no `id` any nav button pointed to, so they rendered on
   every screen. Before wrapping anything, diff every `<div class="card"...>`
   (and `<div id="...">` top-level section) against the nav buttons — if a
   card has no button anywhere that leads to it, find out where it should
   go (check the equivalent section on student.html/teacher.html/parent.html,
   or ask what the card is for) before assigning it a `data-tab`. Do not leave
   any card without a `data-tab` — an untagged card is not covered by the
   `.bmt-tab-panel{display:none}` rule and will show on every tab.

2. **Content living outside the login-gated container.** On parent.html, the
   "Pay for a Student" card and the payment-account-numbers strip were
   siblings of `#portal` (the div whose `display:none/block` is what actually
   gates logged-in content) instead of children of it — meaning they rendered
   for logged-out guests too, sitting right next to the login form. Check
   admin.html for any card/section that sits after the portal container's
   closing `</div>` but before the page's closing tags — if you find one, move
   it inside the portal container. (Tagging it `bmt-tab-panel` also happens to
   fix this as a side effect, since the panel defaults to `display:none`
   regardless of DOM position — but move it inside the portal container
   anyway for structural cleanliness.)

3. **An element whose `display` is toggled by JS directly (`el.style.display=...`)
   must never itself carry `bmt-tab-panel`.** Inline styles set by JS always
   beat the `.bmt-tab-panel{display:none}` CSS rule (no `!important` is used,
   matching student.html's original pattern), so if you tag such an element
   directly, it will still show up on other tabs once its inline style has
   been set to `block` at least once. Recognize this pattern in admin.html by
   grepping for `.style.display` in admin.js and cross-referencing which
   element IDs are toggled that way (parent.html's `#detail` — the child
   detail drill-down — was one; likely candidates on admin.html: any
   "detail view" / "expanded row" / modal-like panel toggled from a list).
   Fix: nest that element as a plain child inside a genuine `bmt-tab-panel`
   wrapper (a parent `<div class="bmt-tab-panel" data-tab="x">` that itself is
   never touched by `.style.display`), the same way teacher.html nests
   `#assignmentSubmissionsPanel` inside the `work` tab and we nested `#detail`
   inside parent.html's `children` tab. A `display:none` parent hides all
   descendants regardless of the descendants' own inline `display` value —
   this is the mechanism that makes nesting the correct fix.

## Required verification before calling it done

Do all of these — they are cheap and they are what caught every bug above:

1. **Tag balance.** Run a real HTML parser (not a naive `<` count) over the
   file and confirm zero unclosed/mismatched tags (Python's `html.parser.HTMLParser`,
   walking a tag stack, works fine for this).
2. **`data-tab` coverage.** `grep -oP 'data-tab="\K[^"]+' admin.html | sort | uniq -c`
   — every value should appear at least twice (once on a nav button, once on
   at least one content panel). If a nav item's tab value appears only once,
   its panel is missing (or vice versa).
3. **No leftover old nav calls.** `grep -n 'bmtGo(' admin.html` must return
   nothing once you're done.
4. **JS syntax.** `node --check` on `static/admin.js` and on every extracted
   inline `<script>` block (module scripts need to be saved as `.mjs`, not
   checked with `--input-type`).
5. **Visual regression test — do this, don't skip it.** Render the page with
   Playwright (already installed in this environment, chromium included) at a
   phone viewport (390x844), with a small load-time override script that
   force-shows the portal container and fills 2-3 sample values into empty
   containers (Firebase auth needs network, which this sandbox doesn't have,
   so it will never resolve on its own — the override just simulates
   "already logged in" for screenshot purposes; never edit the real file with
   this override, keep it in a throwaway copy). Screenshot every tab, and
   specifically re-run the bug-3 scenario: open whatever JS-toggled
   detail/expanded view exists, screenshot it, then switch to a different tab
   via `bmtSwitchTab` and screenshot again — confirm the detail view is gone.
   This exact test caught the parent.html `#detail` bug before it shipped.

## General rules (unchanged from v1, still apply)

- Never rename an existing element `id` — all app JS binds to IDs directly.
- SVG icon style: `viewBox="0 0 24 24" fill="none" stroke="currentColor"
  stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"`, sized via
  inline `style="width:Npx;height:Npx"` or the shared `.bmt-icon` class.
- Keep one `<h1>`/page title per screen — never repeat the brand name
  ("Bright Mind Tutor") in two headers on the same screen; use "Admin Portal"
  for the inner dashboard header.
- Strip decorative emoji the same way as elsewhere: keep any real emoji-picker
  content, star and close-icon glyphs; remove everything else; convert
  icon-only buttons (dark mode toggle, etc.) to the shared inline SVG style.


## Admin conversion notes (added after completion)

- Tab grouping used (values unchanged from the agreed list): `controlcenter` = AI Copilot, Control
  Center 2.0, Organization Logo; `students` = Registered Students (moved OUT of the Control Center card,
  where it was nested — a nested panel can never show while its parent tab is hidden);
  `teachers` = Teacher Requests, Active Roster; `payments` = Payment Approvals, Service Prices;
  `digitallibrary` = Library browser, Upload Book, Upload Video, Exam Archive; `scanner` = Smart Quiz
  Scanner, Add Quiz; `challenges` = Question Bank, Challenge Manager; `growthmedia` = Development media;
  `telegram` = all five Telegram cards; `community` = Push, Announcements, Live Stream, Chat Moderation;
  `parentlinking` = Parent-Student Linking.
- `static/admin.js`: `scrollToAdminSection` now delegates to the tab-aware `bmtShowSection()` (defined in the
  inline script of admin.html) and `#adminIdentity` is filled with the signed-in admin's email.
- Bug-3 check: no top-level admin card is toggled with `el.style.display`; the student-detail view is a
  body-level modal (`#studentDetailModal`), so it is unaffected by tab panels.
- Known, untouched: `admin.js` uses `signInWithCustomToken` in the legacy-claims sign-in fallback but does not
  import it from firebase-auth.js.
- Emoji inside JS-rendered strings (admin*.js) were left as-is, same as teacher.js/student.js.
