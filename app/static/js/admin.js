/* Administration page (wf-6). Conveniences only: the server checks everything again.
 *
 * 1. Add User shows Access Level and Department only when the Role is Administrator (D19).
 * 2. Deactivate, New temp password, and Delist ask for confirmation first.
 */
(function () {
  "use strict";

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
  });
})();
