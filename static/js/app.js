/* Crowd Operations dashboard: polling, DOM binding, zone overlay, heatmap. */
(function () {
  "use strict";

  const $ = function (id) {
    return document.getElementById(id);
  };

  const els = {
    statusDot: $("statusDot"),
    sourceName: $("sourceName"),
    fpsValue: $("fpsValue"),
    eventTotal: $("eventTotal"),
    clock: $("clock"),
    liveBadge: $("liveBadge"),
    liveText: $("liveText"),
    railHealth: $("railHealth"),
    video: $("video"),
    videoEmpty: $("videoEmpty"),
    videoEmptyText: $("videoEmptyText"),
    heatmap: $("heatmap"),
    zoneOverlay: $("zoneOverlay"),
    alertBanner: $("alertBanner"),
    alertTitle: $("alertTitle"),
    alertText: $("alertText"),
    frameMeta: $("frameMeta"),
    cameraTitle: $("cameraTitle"),
    kpiOccupancy: $("kpiOccupancy"),
    kpiPeak: $("kpiPeak"),
    kpiAlerts: $("kpiAlerts"),
    kpiFps: $("kpiFps"),
    zoneCount: $("zoneCount"),
    zoneList: $("zoneList"),
    chartNow: $("chartNow"),
    headcountChart: $("headcountChart"),
    eventBody: $("eventBody"),
    eventEmpty: $("eventEmpty"),
    eventFilters: $("eventFilters"),
    clearEvents: $("clearEvents"),
    eventsPanel: $("eventsPanel"),
    railEvents: $("railEvents"),
  };

  const state = {
    stats: null,
    events: [],
    history: [],
    peak: 0,
    filter: "all",
    selectedZone: null,
    layers: { zones: true, heat: false },
    lastStatsAt: 0,
    lastChartTs: 0,
  };

  function init() {
    CrowdChart.init(els.headcountChart);

    updateClock();
    window.setInterval(updateClock, 1000);
    window.setInterval(checkStale, 1000);
    window.setInterval(fetchStats, 1000);
    window.setInterval(fetchEvents, 2000);

    bindFilters();
    bindLayers();
    bindZoneSelection();
    bindVideoRetry();
    bindClearEvents();
    applyHashLayers();

    els.railEvents.addEventListener("click", function () {
      els.eventsPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });

    fetchStats();
    fetchEvents();
  }

  /* ---------- data ---------- */

  function fetchStats() {
    fetch("/api/stats", { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (stats) {
        state.stats = stats;
        state.lastStatsAt = Date.now();
        document.body.classList.remove("stale");
        renderStats(stats);
      })
      .catch(function () {
        if (state.lastStatsAt && Date.now() - state.lastStatsAt > 3500) {
          document.body.classList.add("stale");
        }
      });
  }

  function fetchEvents() {
    fetch("/api/events?limit=60", { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (events) {
        state.events = Array.isArray(events) ? events : [];
        renderEvents();
      })
      .catch(function () {
        /* keep the last known log on screen */
      });
  }

  /* ---------- render ---------- */

  function renderStats(stats) {
    const live = stats.status === "live" && stats.frame && stats.frame.w > 0;

    els.videoEmpty.hidden = live;
    if (!live) {
      els.videoEmptyText.textContent =
        stats.status === "offline"
          ? "Video source unavailable"
          : stats.status === "ended"
            ? "Video ended — restarting…"
            : "Connecting to stream…";
    }

    els.statusDot.className = "status-dot" + (live ? "" : " offline");
    els.railHealth.style.background = live ? "var(--ok)" : "var(--crit)";
    els.liveBadge.classList.toggle("offline", !live);
    els.liveText.textContent = live ? "LIVE" : "OFFLINE";

    els.sourceName.textContent = stats.source || "Source";
    if (stats.camera && els.cameraTitle) {
      els.cameraTitle.textContent = stats.camera;
    }
    els.fpsValue.textContent = formatNumber(stats.fps, 1);
    els.kpiFps.textContent = formatNumber(stats.fps, 1);
    els.eventTotal.textContent = stats.events_total || 0;
    els.frameMeta.textContent =
      stats.frame && stats.frame.w
        ? stats.frame.w + "×" + stats.frame.h + " · " + formatNumber(stats.fps, 1) + " FPS"
        : "—";

    const occupancy = stats.occupancy || 0;
    const zones = stats.zones || [];
    const activeAlerts = zones.filter(function (zone) {
      return zone.status !== "ok";
    }).length;

    state.peak = Math.max(state.peak, occupancy);
    els.kpiOccupancy.textContent = occupancy;
    els.kpiPeak.textContent = state.peak;
    els.kpiAlerts.textContent = activeAlerts;
    els.kpiAlerts.classList.toggle("alert", activeAlerts > 0);
    els.chartNow.textContent = occupancy;

    renderZoneList(zones);
    renderZoneOverlay(zones);
    renderAlert(zones);

    if (state.layers.heat) {
      renderHeatmap(stats.heatmap);
    }

    if (stats.ts && stats.ts !== state.lastChartTs) {
      state.lastChartTs = stats.ts;
      state.history.push({
        label: new Date(stats.ts * 1000).toLocaleTimeString([], { hour12: false }),
        value: occupancy,
      });
      if (state.history.length > 60) state.history.shift();
      CrowdChart.update(state.history);
    }
  }

  function renderZoneList(zones) {
    els.zoneCount.textContent =
      zones.length + (zones.length === 1 ? " zone" : " zones");

    els.zoneList.innerHTML = zones
      .map(function (zone) {
        const ratio = Math.min(1, zone.count / Math.max(1, zone.capacity));
        const percent = Math.round(ratio * 100);
        const swatch =
          zone.status === "critical"
            ? "var(--crit)"
            : zone.status === "warning"
              ? "var(--warn)"
              : "var(--accent)";

        return (
          '<div class="zone-row' +
          (state.selectedZone === zone.name ? " selected" : "") +
          '" data-zone="' +
          escapeAttr(zone.name) +
          '">' +
          '<div class="zone-top">' +
          '<span class="zone-name"><span class="zone-swatch" style="background:' +
          swatch +
          '"></span>' +
          escapeHtml(zone.name) +
          "</span>" +
          '<span class="zone-count">' +
          zone.count +
          " / " +
          zone.capacity +
          "</span>" +
          "</div>" +
          '<div class="zone-bar"><div class="zone-fill ' +
          zone.status +
          '" style="width:' +
          percent +
          '%"></div></div>' +
          '<div class="zone-meta"><span>' +
          formatNumber(zone.density, 1) +
          " p/m²</span><span>dwell " +
          formatNumber(zone.max_dwell, 0) +
          "s</span><span>" +
          formatNumber(zone.velocity, 1) +
          " m/s</span></div>" +
          "</div>"
        );
      })
      .join("");
  }

  function renderZoneOverlay(zones) {
    const svg = els.zoneOverlay;
    const namespace = "http://www.w3.org/2000/svg";
    svg.innerHTML = "";

    zones.forEach(function (zone) {
      const polygon = zone.polygon || [];
      if (polygon.length < 3) return;

      const points = polygon
        .map(function (point) {
          return point[0] * 160 + "," + point[1] * 90;
        })
        .join(" ");

      const shape = document.createElementNS(namespace, "polygon");
      shape.setAttribute("points", points);
      shape.setAttribute("data-zone", zone.name);
      shape.setAttribute(
        "fill",
        zone.status === "critical"
          ? "rgba(251, 113, 133, 0.14)"
          : zone.status === "warning"
            ? "rgba(251, 191, 36, 0.12)"
            : "rgba(34, 211, 238, 0.07)"
      );
      shape.setAttribute(
        "stroke",
        zone.status === "critical"
          ? "#fb7185"
          : zone.status === "warning"
            ? "#fbbf24"
            : "rgba(34, 211, 238, 0.55)"
      );
      shape.setAttribute("stroke-width", "0.55");
      shape.setAttribute(
        "stroke-dasharray",
        zone.status === "ok" ? "2 1.5" : "0"
      );
      if (state.selectedZone === zone.name) shape.classList.add("selected");
      svg.appendChild(shape);

      const centreX =
        (polygon.reduce(function (sum, point) {
          return sum + point[0];
        }, 0) /
          polygon.length) *
        160;
      const centreY =
        (polygon.reduce(function (sum, point) {
          return sum + point[1];
        }, 0) /
          polygon.length) *
        90;

      const label = document.createElementNS(namespace, "text");
      label.setAttribute("x", centreX);
      label.setAttribute("y", centreY);
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("font-size", "3.4");
      label.setAttribute(
        "fill",
        zone.status === "critical"
          ? "#ffc2cb"
          : zone.status === "warning"
            ? "#ffe4a3"
            : "#bdf3fa"
      );
      label.textContent =
        zone.name + " · " + zone.count + "/" + zone.capacity;
      svg.appendChild(label);
    });
  }

  function renderAlert(zones) {
    const critical = zones.find(function (zone) {
      return zone.status === "critical";
    });
    const warning = zones.find(function (zone) {
      return zone.status === "warning";
    });
    const top = critical || warning;

    if (!top) {
      els.alertBanner.hidden = true;
      return;
    }

    els.alertBanner.hidden = false;
    els.alertBanner.classList.toggle("warning", top.status === "warning");
    els.alertTitle.textContent =
      top.status === "critical" ? "Congestion alert" : "Capacity warning";
    els.alertText.textContent =
      top.name +
      " · " +
      top.count +
      "/" +
      top.capacity +
      " people · " +
      formatNumber(top.density, 1) +
      " people/m²";
  }

  function renderHeatmap(heatmap) {
    if (!heatmap || !state.layers.heat) return;

    const canvas = els.heatmap;
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);

    const context = canvas.getContext("2d");
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, rect.width, rect.height);

    const cols = heatmap.cols || 1;
    const rows = heatmap.rows || 1;
    const values = heatmap.values || [];
    const cellWidth = rect.width / cols;
    const cellHeight = rect.height / rows;

    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < cols; col += 1) {
        const value = values[row * cols + col] || 0;
        if (value < 0.06) continue;
        context.fillStyle = heatColor(value);
        context.fillRect(
          col * cellWidth,
          row * cellHeight,
          cellWidth + 1,
          cellHeight + 1
        );
      }
    }
  }

  function renderEvents() {
    const filtered = state.events.filter(function (event) {
      const severityMatch =
        state.filter === "all" || event.severity === state.filter;
      const zoneMatch =
        !state.selectedZone || event.zone === state.selectedZone;
      return severityMatch && zoneMatch;
    });

    els.eventEmpty.hidden = filtered.length > 0;
    if (!filtered.length) {
      els.eventEmpty.textContent = state.selectedZone
        ? "No events for " + state.selectedZone + " with this filter."
        : "No events yet — the log will populate when a threshold is crossed.";
    }

    els.eventBody.innerHTML = filtered
      .map(function (event) {
        const time = new Date(event.ts * 1000).toLocaleTimeString([], {
          hour12: false,
        });
        const snapshot = event.snapshot
          ? '<a href="/snapshots/' +
            encodeURIComponent(event.snapshot) +
            '" target="_blank" rel="noopener"><img class="snapshot" src="/snapshots/' +
            encodeURIComponent(event.snapshot) +
            '" alt="Event evidence"></a>'
          : '<span class="no-snapshot">—</span>';

        return (
          '<div class="event-row">' +
          '<span class="event-time">' +
          time +
          "</span>" +
          '<span class="event-zone">' +
          escapeHtml(event.zone) +
          "</span>" +
          '<span class="event-msg"><span class="badge ' +
          escapeAttr(event.severity) +
          '">' +
          escapeHtml(event.severity) +
          "</span>" +
          escapeHtml(event.message) +
          "</span>" +
          '<span class="event-count">' +
          event.count +
          "</span>" +
          '<span class="event-evidence">' +
          snapshot +
          "</span>" +
          "</div>"
        );
      })
      .join("");
  }

  /* ---------- interactions ---------- */

  function bindFilters() {
    els.eventFilters.addEventListener("click", function (event) {
      const button = event.target.closest("[data-filter]");
      if (!button) return;
      state.filter = button.getAttribute("data-filter");
      Array.prototype.forEach.call(
        els.eventFilters.querySelectorAll("[data-filter]"),
        function (candidate) {
          candidate.classList.toggle("active", candidate === button);
        }
      );
      renderEvents();
    });
  }

  function bindLayers() {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-layer]"),
      function (button) {
        button.addEventListener("click", function () {
          const layer = button.getAttribute("data-layer");
          state.layers[layer] = !state.layers[layer];
          button.classList.toggle("active", state.layers[layer]);
          applyLayers();
        });
      }
    );
  }

  function applyLayers() {
    els.zoneOverlay.style.display = state.layers.zones ? "" : "none";
    els.heatmap.classList.toggle("visible", state.layers.heat);
    if (state.layers.heat && state.stats) {
      renderHeatmap(state.stats.heatmap);
    }
  }

  function applyHashLayers() {
    const hash = window.location.hash.toLowerCase();
    if (hash.includes("heat")) state.layers.heat = true;
    if (hash.includes("nozones")) state.layers.zones = false;

    Array.prototype.forEach.call(
      document.querySelectorAll("[data-layer]"),
      function (button) {
        const layer = button.getAttribute("data-layer");
        button.classList.toggle("active", Boolean(state.layers[layer]));
      }
    );
    applyLayers();
  }

  function bindZoneSelection() {
    function select(event) {
      const target = event.target.closest("[data-zone]");
      if (!target) return;
      const name = target.getAttribute("data-zone");
      state.selectedZone = state.selectedZone === name ? null : name;
      if (state.stats) {
        renderZoneList(state.stats.zones || []);
        renderZoneOverlay(state.stats.zones || []);
      }
      renderEvents();
    }

    els.zoneOverlay.addEventListener("click", select);
    els.zoneList.addEventListener("click", select);
  }

  function bindVideoRetry() {
    els.video.addEventListener("error", function () {
      els.videoEmpty.hidden = false;
      els.videoEmptyText.textContent = "Stream unavailable — reconnecting…";
      window.setTimeout(function () {
        els.video.src = "/video?t=" + Date.now();
      }, 2500);
    });
  }

  function bindClearEvents() {
    if (!els.clearEvents) return;
    els.clearEvents.addEventListener("click", function () {
      if (!window.confirm("Delete every logged event and evidence image?")) {
        return;
      }
      fetch("/api/events/clear", { method: "POST" })
        .then(function (response) {
          if (!response.ok) throw new Error("HTTP " + response.status);
          state.events = [];
          state.history = [];
          state.peak = 0;
          state.lastChartTs = 0;
          CrowdChart.update(state.history);
          renderEvents();
          fetchStats();
          fetchEvents();
        })
        .catch(function () {
          window.alert("Could not clear the log. Is the server running?");
        });
    });
  }

  /* ---------- helpers ---------- */

  function updateClock() {
    els.clock.textContent = new Date().toLocaleTimeString([], {
      hour12: false,
    });
  }

  function checkStale() {
    if (state.lastStatsAt && Date.now() - state.lastStatsAt > 4000) {
      document.body.classList.add("stale");
    }
  }

  function heatColor(value) {
    // Gamma-shift each weight so the long tail of lightly-used cells still
    // reads on screen, then use the same tone for colour and alpha.
    const tone = Math.pow(value, 0.6);
    const stops = [
      [0.0, [34, 211, 238]],
      [0.5, [251, 191, 36]],
      [1.0, [251, 113, 133]],
    ];
    let lower = stops[0];
    let upper = stops[stops.length - 1];

    for (let index = 0; index < stops.length - 1; index += 1) {
      if (tone >= stops[index][0] && tone <= stops[index + 1][0]) {
        lower = stops[index];
        upper = stops[index + 1];
        break;
      }
    }

    const span = Math.max(1e-6, upper[0] - lower[0]);
    const ratio = (tone - lower[0]) / span;
    const rgb = lower[1].map(function (channel, index) {
      return Math.round(channel + (upper[1][index] - channel) * ratio);
    });

    return (
      "rgba(" +
      rgb[0] +
      "," +
      rgb[1] +
      "," +
      rgb[2] +
      "," +
      (0.22 + 0.78 * tone).toFixed(2) +
      ")"
    );
  }

  function formatNumber(value, digits) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits) : "0";
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (character) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[character];
    });
  }

  function escapeAttr(value) {
    return escapeHtml(value);
  }

  init();
})();
