/* Stocks pages (wf-1): draws the sparklines and the price chart with Chart.js.
 *
 * The numbers come from the <script type="application/json" id="chart-data"> block, so no
 * inline script is needed (CSP). Colours come from Bootstrap's CSS variables and are redrawn
 * when the sun/moon button switches the theme (D13). Without JavaScript the page still shows
 * every price and change as text.
 */
(function () {
  "use strict";

  var charts = [];

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function colours() {
    return {
      up: cssVar("--bs-success"),
      down: cssVar("--bs-danger"),
      line: cssVar("--bs-primary"),
      grid: cssVar("--bs-border-color"),
      text: cssVar("--bs-secondary-color"),
    };
  }

  function numbers(values) {
    return values.map(Number);
  }

  function sparkline(canvas, values) {
    var c = colours();
    var falling = values.length > 1 && values[values.length - 1] < values[0];
    var chart = new Chart(canvas, {
      type: "line",
      data: { labels: values.map(function (_, i) { return i; }), datasets: [{ data: values, borderWidth: 1.5, pointRadius: 0 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, events: [],
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false } },
      },
    });
    chart.$recolour = function (c) { chart.data.datasets[0].borderColor = falling ? c.down : c.up; };
    chart.$recolour(c);
    chart.update();
    return chart;
  }

  function priceChart(canvas, history) {
    var money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
    var chart = new Chart(canvas, {
      type: "line",
      data: { labels: history.labels, datasets: [{ label: "Close", data: numbers(history.values), borderWidth: 2, pointRadius: 0, tension: 0.1 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: function (item) { return money.format(item.parsed.y); } } },
        },
        scales: {
          x: { title: { display: true, text: "Dates" }, ticks: { maxTicksLimit: 6 } },
          y: { title: { display: true, text: "Value ($)" }, ticks: { callback: function (v) { return money.format(v); } } },
        },
      },
    });
    chart.$recolour = function (c) {
      chart.data.datasets[0].borderColor = c.line;
      ["x", "y"].forEach(function (axis) {
        chart.options.scales[axis].grid.color = c.grid;
        chart.options.scales[axis].ticks.color = c.text;
        chart.options.scales[axis].title.color = c.text;
      });
    };
    chart.$recolour(colours());
    chart.update();
    return chart;
  }

  document.addEventListener("DOMContentLoaded", function () {
    var block = document.getElementById("chart-data");
    if (!block || typeof Chart === "undefined") {
      return;
    }
    var data = JSON.parse(block.textContent);
    Array.prototype.forEach.call(document.querySelectorAll("canvas[data-spark]"), function (canvas) {
      var values = numbers(data.sparks[canvas.getAttribute("data-spark")] || []);
      if (values.length > 1) {
        charts.push(sparkline(canvas, values));
      }
    });
    var main = document.getElementById("price-chart");
    if (main && data.history) {
      charts.push(priceChart(main, data.history));
    }
    // Redraw in the new theme's colours when the sun/moon button is used.
    new MutationObserver(function () {
      var c = colours();
      charts.forEach(function (chart) { chart.$recolour(c); chart.update(); });
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-bs-theme"] });
  });
})();
