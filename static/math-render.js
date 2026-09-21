/* V31.108: Shared KaTeX math-rendering wiring for every BMT dashboard
   (student, teacher, parent, admin).

   Why a MutationObserver instead of editing every innerHTML call site:
   AI Tutor replies, Homework Coach/Image answers, Study Plan output, quiz and
   exam question text, and community/private chat messages are all rendered by
   dozens of separate innerHTML assignments spread across student.js/teacher.js/
   admin.js. Patching each call site individually is fragile (easy to miss one,
   easy to break on the next edit). This file renders LaTeX ($...$, $$...$$,
   \\(...\\), \\[...\\]) wherever it appears in the DOM, automatically, the moment
   it is inserted - regardless of which script or API response put it there.

   Load order requirement: this script must load AFTER katex.min.js and
   auto-render.min.js (see the <script defer> tags in each template's <head>).
   All three are loaded with `defer`, which guarantees they execute in source
   order after the HTML is parsed - the same fix pattern already used elsewhere
   in BMT to avoid defer/async race conditions between dependent CDN scripts. */
(function () {
  var RENDER_OPTIONS = {
    delimiters: [
      { left: '$$', right: '$$', display: true },
      { left: '\\[', right: '\\]', display: true },
      { left: '$', right: '$', display: false },
      { left: '\\(', right: '\\)', display: false }
    ],
    throwOnError: false,
    ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
  };

  function renderMath(target) {
    if (typeof window.renderMathInElement !== 'function') return;
    var root = (target && target.nodeType === 1) ? target : document.body;
    try {
      window.renderMathInElement(root, RENDER_OPTIONS);
    } catch (e) {
      /* Malformed formulas in AI-generated or user-typed content must never
         break the dashboard - fail silently and leave the raw text visible. */
    }
  }

  var pending = false;
  function scheduleRender() {
    if (pending) return;
    pending = true;
    var raf = window.requestAnimationFrame || function (fn) { return setTimeout(fn, 50); };
    raf(function () {
      pending = false;
      renderMath(document.body);
    });
  }

  function start() {
    renderMath(document.body);
    if (!document.body || typeof MutationObserver === 'undefined') return;
    var observer = new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        var m = mutations[i];
        if ((m.addedNodes && m.addedNodes.length) || m.type === 'characterData') {
          scheduleRender();
          return;
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  // Exposed so any call site can force an immediate render right after it
  // writes content, instead of waiting one animation frame for the observer.
  window.BMTRenderMath = renderMath;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
