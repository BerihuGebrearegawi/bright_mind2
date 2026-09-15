/*
 Bright Mind Tutor V30.26
 Shared UI migration helper.
 Non-destructive: it only adds classes/attributes to elements explicitly
 marked for migration.
*/
(function () {
  "use strict";

  function migrate(root) {
    root = root || document;

    root.querySelectorAll("[data-bmt-page]").forEach(function (page) {
      page.classList.add("bmt-page");

      var header = page.querySelector("[data-bmt-page-header]");
      if (header) header.classList.add("bmt-page-header");

      page.querySelectorAll("[data-bmt-card]").forEach(function (el) {
        el.classList.add("bmt-card");
      });

      page.querySelectorAll("[data-bmt-table]").forEach(function (el) {
        el.classList.add("bmt-table-wrap");
        var table = el.querySelector("table");
        if (table) table.classList.add("bmt-table");
      });

      page.querySelectorAll("[data-bmt-primary]").forEach(function (el) {
        el.classList.add("bmt-btn", "bmt-btn-primary");
      });

      page.querySelectorAll("[data-bmt-secondary]").forEach(function (el) {
        el.classList.add("bmt-btn", "bmt-btn-secondary");
      });

      page.querySelectorAll("[data-bmt-accent]").forEach(function (el) {
        el.classList.add("bmt-btn", "bmt-btn-accent");
      });

      page.querySelectorAll("[data-bmt-status]").forEach(function (el) {
        el.classList.add("bmt-badge");
      });

      page.querySelectorAll("[data-bmt-focus]").forEach(function (el) {
        el.classList.add("bmt-focus");
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { migrate(); });
  } else {
    migrate();
  }

  window.BMT_UI_MIGRATION = { migrate: migrate };
})();
