/* Headcount trend chart. Uses the vendored Chart.js build; falls back to a
   simple canvas polyline if the vendor file is ever missing. */
(function () {
  "use strict";

  let chart = null;
  let fallback = null;

  function init(canvas) {
    if (!canvas) return;

    if (window.Chart) {
      const context = canvas.getContext("2d");
      const gradient = context.createLinearGradient(0, 0, 0, 190);
      gradient.addColorStop(0, "rgba(34, 211, 238, 0.26)");
      gradient.addColorStop(1, "rgba(34, 211, 238, 0)");

      chart = new window.Chart(context, {
        type: "line",
        data: {
          labels: [],
          datasets: [
            {
              data: [],
              borderColor: "#22d3ee",
              backgroundColor: gradient,
              fill: true,
              tension: 0.35,
              pointRadius: 0,
              pointHoverRadius: 3,
              pointHoverBackgroundColor: "#22d3ee",
              borderWidth: 1.8,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          interaction: { intersect: false, mode: "index" },
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: "rgba(8, 14, 20, 0.96)",
              borderColor: "rgba(34, 211, 238, 0.28)",
              borderWidth: 1,
              titleColor: "#e9f0f6",
              bodyColor: "#8ba0b3",
              titleFont: { size: 10, family: "Inter" },
              bodyFont: { size: 10, family: "JetBrains Mono" },
              padding: 8,
              displayColors: false,
              callbacks: {
                label: function (item) {
                  return item.parsed.y + " people";
                },
              },
            },
          },
          scales: {
            x: { display: false, grid: { display: false } },
            y: {
              beginAtZero: true,
              suggestedMax: 10,
              grid: { color: "rgba(28, 42, 55, 0.55)", drawBorder: false },
              border: { display: false },
              ticks: {
                color: "#5d7386",
                font: { size: 9, family: "JetBrains Mono" },
                maxTicksLimit: 4,
                precision: 0,
                padding: 4,
              },
            },
          },
        },
      });
      return;
    }

    fallback = { canvas: canvas, history: [] };
  }

  function update(history) {
    if (chart) {
      chart.data.labels = history.map(function (point) {
        return point.label;
      });
      chart.data.datasets[0].data = history.map(function (point) {
        return point.value;
      });
      chart.update("none");
      return;
    }

    if (fallback) {
      fallback.history = history;
      drawFallback();
    }
  }

  function drawFallback() {
    const canvas = fallback.canvas;
    const context = canvas.getContext("2d");
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, rect.width);
    const height = Math.max(1, rect.height);

    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, width, height);

    const values = fallback.history.map(function (point) {
      return point.value;
    });
    if (values.length < 2) return;

    const max = Math.max(5, ...values);
    const pad = 6;
    context.beginPath();
    values.forEach(function (value, index) {
      const x = pad + (index / (values.length - 1)) * (width - pad * 2);
      const y = height - pad - (value / max) * (height - pad * 2);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.strokeStyle = "#22d3ee";
    context.lineWidth = 1.6;
    context.stroke();
  }

  window.CrowdChart = { init: init, update: update };
})();
