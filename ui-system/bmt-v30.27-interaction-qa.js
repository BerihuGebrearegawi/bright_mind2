/*
 Bright Mind Tutor V30.27
 Navigation, forms, tables and interaction QA helpers.
 Non-destructive: opt-in via data-bmt-* attributes.
*/
(function () {
  "use strict";

  function safeText(el, text) {
    if (el) el.textContent = text;
  }

  function init(root) {
    root = root || document;

    // Prevent accidental double-submit while preserving the original handler.
    root.querySelectorAll("form[data-bmt-protect-submit]").forEach(function (form) {
      if (form.dataset.bmtSubmitBound === "1") return;
      form.dataset.bmtSubmitBound = "1";

      form.addEventListener("submit", function () {
        var submit = form.querySelector('button[type="submit"], input[type="submit"]');
        if (!submit) return;
        submit.dataset.bmtOriginalText = submit.textContent || submit.value || "";
        submit.disabled = true;
        if ("value" in submit) submit.value = "Processing...";
        else submit.textContent = "Processing...";
        window.setTimeout(function () {
          submit.disabled = false;
          if ("value" in submit) submit.value = submit.dataset.bmtOriginalText;
          else submit.textContent = submit.dataset.bmtOriginalText;
        }, 8000);
      });
    });

    // Accessible client-side required-field hints.
    root.querySelectorAll("[data-bmt-validate]").forEach(function (form) {
      if (form.dataset.bmtValidateBound === "1") return;
      form.dataset.bmtValidateBound = "1";

      form.addEventListener("submit", function (event) {
        var invalid = form.querySelector(":invalid");
        if (!invalid) return;

        event.preventDefault();
        invalid.setAttribute("aria-invalid", "true");
        invalid.focus();

        var msg = form.querySelector("[data-bmt-validation-message]");
        if (msg) safeText(msg, "Please check the highlighted field and try again.");
      });
    });

    // Keyboard-friendly sortable tables.
    root.querySelectorAll("table[data-bmt-sortable]").forEach(function (table) {
      table.querySelectorAll("thead th[data-bmt-sort-key]").forEach(function (th) {
        if (th.dataset.bmtSortBound === "1") return;
        th.dataset.bmtSortBound = "1";
        th.tabIndex = 0;
        th.setAttribute("role", "button");

        function activate() {
          var event = new CustomEvent("bmt:sort", {
            bubbles: true,
            detail: { key: th.dataset.bmtSortKey, table: table }
          });
          table.dispatchEvent(event);
        }

        th.addEventListener("click", activate);
        th.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            activate();
          }
        });
      });
    });

    // Navigation links: mark current route without replacing routing logic.
    root.querySelectorAll("[data-bmt-nav-link]").forEach(function (link) {
      if (link.dataset.bmtNavBound === "1") return;
      link.dataset.bmtNavBound = "1";

      try {
        var current = window.location.pathname.replace(/\/+$/, "") || "/";
        var target = new URL(link.href, window.location.origin).pathname.replace(/\/+$/, "") || "/";
        if (current === target) {
          link.classList.add("active");
          link.setAttribute("aria-current", "page");
        }
      } catch (_) {}
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { init(); });
  } else {
    init();
  }

  window.BMT_INTERACTION_QA = { init: init };
})();
