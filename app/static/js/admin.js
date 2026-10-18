/* Administration page (wf-6). Conveniences only: the server checks everything again.
 *
 * 1. Add User shows Access Level and Department only when the Role is Administrator (D19).
 * 2. Deactivate, New temp password, and Delist ask for confirmation first.
 * 3. The Market Time Trading Days button summarises the checked days, like market.days_short() (D29).
 */
(function () {
  "use strict";

  /* "Mon–Fri", "Every day", "Mon, Wed, Fri": runs of three or more days become a range. */
  function daysShort(names, checked) {
    var runs = [];
    checked.forEach(function (d) {
      var last = runs[runs.length - 1];
      if (last && last[1] === d - 1) {
        last[1] = d;
      } else {
        runs.push([d, d]);
      }
    });
    if (checked.length === 0) {
      return "No days selected";
    }
    if (checked.length === 7) {
      return "Every day";
    }
    var parts = [];
    runs.forEach(function (run) {
      if (run[1] - run[0] >= 2) {
        parts.push(names[run[0]] + "–" + names[run[1]]);
      } else {
        for (var d = run[0]; d <= run[1]; d++) {
          parts.push(names[d]);
        }
      }
    });
    return parts.join(", ");
  }

  document.addEventListener("DOMContentLoaded", function () {
    var role = document.querySelector("[data-role]");
    if (role) {
      var fields = document.querySelectorAll("[data-admin-field]");
      var sync = function () {
        Array.prototype.forEach.call(fields, function (field) {
          field.hidden = role.value !== "Administrator";
        });
      };
      role.addEventListener("change", sync);
      sync();
    }

    Array.prototype.forEach.call(document.querySelectorAll("form[data-confirm]"), function (form) {
      form.addEventListener("submit", function (event) {
        if (!window.confirm(form.getAttribute("data-confirm"))) {
          event.preventDefault();
        }
      });
    });

    var days = document.querySelector("[data-days-dropdown]");
    if (days) {
      var label = days.querySelector("[data-days-label]");
      var boxes = days.querySelectorAll("input[data-day]");
      var names = Array.prototype.map.call(boxes, function (box) { return box.getAttribute("data-day"); });
      var summarise = function () {
        var checked = [];
        Array.prototype.forEach.call(boxes, function (box, i) {
          if (box.checked) {
            checked.push(i);
          }
        });
        label.textContent = daysShort(names, checked);
      };
      Array.prototype.forEach.call(boxes, function (box) {
        box.addEventListener("change", summarise);
      });
    }
  });
})();
