// Draws the charts on the Account, Positions and Stocks pages, using Chart.js (loaded from the CDN).
//
// The Python code puts the numbers into the page as JSON, in:
//     <script type="application/json" id="chart-data"> ... </script>
// This file reads them and draws whichever charts the page has:
//
//     <canvas id="value-chart">          portfolio value over time    (Account, Positions)
//     <canvas id="price-chart">          one stock's price, last year (a stock's page)
//     <canvas data-sparkline="AURA">     a small 30-day chart          (stock lists)
//
// The colours come from Bootstrap, so the charts redraw when the page switches light/dark.

const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const drawnCharts = [];

// A colour from Bootstrap's current theme, for example "--bs-primary".
function bootstrapColour(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// Turns a list of numbers-as-text, like ["42.18", "43.02"], into numbers.
function toNumbers(textList) {
  const numbers = [];
  for (const text of textList) {
    numbers.push(Number(text));
  }
  return numbers;
}

// A full-size line chart with dates along the bottom and dollars up the side.
function drawLineChart(canvas, labels, values) {
  const textColour = bootstrapColour("--bs-secondary-color");
  const gridColour = bootstrapColour("--bs-border-color");
  const lineColour = bootstrapColour("--bs-primary");

  // With only a few points, draw a dot on each so they can be seen (one point makes no line).
  let dotSize = 0;
  if (values.length < 30) {
    dotSize = 3;
  }

  const chart = new Chart(canvas, {
    type: "line",
    data: {
      labels: labels,
      datasets: [{
        data: toNumbers(values),
        borderColor: lineColour,
        pointBackgroundColor: lineColour,
        borderWidth: 2,
        pointRadius: dotSize,
      }],
    },
    options: {
      maintainAspectRatio: false, // fill the box that app.css sizes
      animation: false,
      interaction: { mode: "index", intersect: false }, // hovering anywhere shows the nearest point
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: function (item) {
              return dollars.format(item.parsed.y);
            },
          },
        },
      },
      scales: {
        x: {
          title: { display: true, text: "Date", color: textColour },
          ticks: { maxTicksLimit: 6, color: textColour },
          grid: { color: gridColour },
        },
        y: {
          title: { display: true, text: "Value ($)", color: textColour },
          ticks: {
            color: textColour,
            callback: function (value) {
              return dollars.format(value);
            },
          },
          grid: { color: gridColour },
        },
      },
    },
  });
  drawnCharts.push(chart);
}

// A small chart with no labels: green if the price went up over the 30 days, red if it went down.
function drawSparkline(canvas, values) {
  const numbers = toNumbers(values);
  if (numbers.length < 2) {
    return; // a line needs at least two points
  }

  let colour = bootstrapColour("--bs-success");
  if (numbers[numbers.length - 1] < numbers[0]) {
    colour = bootstrapColour("--bs-danger");
  }

  const labels = [];
  for (let i = 0; i < numbers.length; i++) {
    labels.push(i);
  }

  const chart = new Chart(canvas, {
    type: "line",
    data: {
      labels: labels,
      datasets: [{ data: numbers, borderColor: colour, borderWidth: 1.5, pointRadius: 0 }],
    },
    options: {
      maintainAspectRatio: false,
      animation: false,
      events: [], // no hover effects
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false } },
    },
  });
  drawnCharts.push(chart);
}

function drawAllCharts() {
  const dataBlock = document.getElementById("chart-data");
  if (dataBlock === null) {
    return; // this page has no charts
  }
  const data = JSON.parse(dataBlock.textContent);

  // Account and Positions pages
  const valueCanvas = document.getElementById("value-chart");
  if (valueCanvas !== null) {
    drawLineChart(valueCanvas, data.labels, data.values);
  }

  // A stock's own page
  const priceCanvas = document.getElementById("price-chart");
  if (priceCanvas !== null && data.history) {
    drawLineChart(priceCanvas, data.history.labels, data.history.values);
  }

  // The small charts in the stock lists
  if (data.sparks) {
    for (const canvas of document.querySelectorAll("canvas[data-sparkline]")) {
      const symbol = canvas.getAttribute("data-sparkline");
      drawSparkline(canvas, data.sparks[symbol] || []);
    }
  }
}

// When the light/dark button is pressed, remove the charts and draw them again in the new colours.
function redrawAllCharts() {
  for (const chart of drawnCharts) {
    chart.destroy();
  }
  drawnCharts.length = 0;
  drawAllCharts();
}

document.addEventListener("DOMContentLoaded", function () {
  drawAllCharts();

  // Watch the <html> tag's data-bs-theme attribute, which site.js changes.
  const watcher = new MutationObserver(redrawAllCharts);
  watcher.observe(document.documentElement, { attributes: true, attributeFilter: ["data-bs-theme"] });
});
