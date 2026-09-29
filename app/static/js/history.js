/* History page (wf-8), printing (R13.5).
 *
 * 1. Print asks how far back to print (a small form, user feedback); submitting it loads that whole
 *    range on one page (?print=1), marked data-autoprint, and this opens the print dialog for it.
 *    The print styles in app.css leave out the navigation bar, the filters, and the page selector.
 * 2. While printing (that, or Ctrl+P), the page title becomes "Transaction History - <account> -
 *    <start> to <end>", which browsers use as the default PDF file name. It goes back afterwards,
 *    so the browser tab is unchanged.
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var section = document.querySelector("[data-print-title]");
    if (!section) {
      return;
    }
    var normalTitle = document.title;
    window.addEventListener("beforeprint", function () {
      document.title = section.getAttribute("data-print-title");
    });
    window.addEventListener("afterprint", function () {
      document.title = normalTitle;
    });
    if (section.hasAttribute("data-autoprint")) {
      window.print();
    }
  });
})();
