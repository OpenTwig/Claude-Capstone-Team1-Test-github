/* Summary and Positions pages (wf-3, wf-4). Everything here is a convenience: the numbers are
 * all on the page as text, and the forms work without JavaScript.
 *
 * 1. The value-over-time chart (D23), drawn with Chart.js from the JSON data block and
 *    recoloured when the sun/moon button changes the theme (D13).
 * 2. The Portfolios search box filters the table as you type (R5.5).
 * 3. Picking another portfolio on Positions reloads the page for it.
 * 4. Clicking anywhere on a Portfolios row opens that portfolio's Positions.
 */
(function () {
  "use strict";

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function drawChart() {
    var block = document.getElementById("chart-data");
    var canvas = document.getElementById("value-chart");
    if (!block || !canvas || typeof Chart === "undefined") {
      return;
    }
    var data = JSON.parse(block.textContent);
    var note = document.querySelector("[data-chart-note]");
    if (note && data.values.length < 2) {
      note.hidden = false; // one point is just today's value: say how the chart fills in
    }
    var money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
    var chart = new Chart(canvas, {
      type: "line",
      data: { labels: data.labels, datasets: [{ data: data.values.map(Number), borderWidth: 2, pointRadius: data.values.length < 30 ? 3 : 0, tension: 0.1 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (item) { return money.format(item.parsed.y); } } } },
        scales: {
          x: { title: { display: true, text: "Dates" }, ticks: { maxTicksLimit: 6 } },
          y: { title: { display: true, text: "Value ($)" }, ticks: { callback: function (v) { return money.format(v); } } },
        },
      },
    });
    function recolour() {
      var line = cssVar("--bs-primary"), grid = cssVar("--bs-border-color"), text = cssVar("--bs-secondary-color");
      chart.data.datasets[0].borderColor = line;
      chart.data.datasets[0].pointBackgroundColor = line;
      ["x", "y"].forEach(function (axis) {
        chart.options.scales[axis].grid.color = grid;
        chart.options.scales[axis].ticks.color = text;
        chart.options.scales[axis].title.color = text;
      });
      chart.update();
    }
    recolour();
    new MutationObserver(recolour).observe(document.documentElement, { attributes: true, attributeFilter: ["data-bs-theme"] });
  }

  function wireSearch() {
    var form = document.querySelector("[data-portfolio-search]");
    if (!form) {
      return;
    }
    var input = form.elements.q;
    var rows = document.querySelectorAll("[data-portfolio-row]");
    var none = document.querySelector("[data-no-match]");
    function filter() {
      var needle = input.value.trim().toLowerCase();
      var shown = 0;
      Array.prototype.forEach.call(rows, function (row) {
        var match = row.getAttribute("data-search").toLowerCase().indexOf(needle) !== -1;
        row.hidden = !match;
        shown += match ? 1 : 0;
      });
      if (none) {
        none.hidden = shown > 0;
        none.firstElementChild.textContent = 'No portfolios match "' + input.value.trim() + '".';
      }
    }
    input.addEventListener("input", filter);
    form.addEventListener("submit", function (event) {
      event.preventDefault(); // the table already shows the matches
      filter();
    });
  }

  function wirePortfolioPicker() {
    var form = document.querySelector("[data-portfolio-form]");
    if (!form) {
      return;
    }
    form.querySelector("[data-portfolio-go]").classList.add("d-none");
    form.elements.portfolio.addEventListener("change", function () {
      form.submit();
    });
  }

  function wireRowLinks() {
    Array.prototype.forEach.call(document.querySelectorAll("tr[data-href]"), function (row) {
      row.addEventListener("click", function (event) {
        if (event.target.closest("a, button, input, select") || String(window.getSelection())) {
          return; // a real link was clicked, or the user is selecting text to copy
        }
        window.location.assign(row.getAttribute("data-href"));
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    drawChart();
    wireSearch();
    wirePortfolioPicker();
    wireRowLinks();
  });
})();
