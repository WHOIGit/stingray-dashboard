// Replace a camera frame that fails to load (e.g. frame service unreachable)
// with a short note instead of the browser's broken-image icon. Image error
// events don't bubble, so listen in the capture phase. The flag goes on the
// frame block rather than inserting nodes, so React's DOM stays untouched.
document.addEventListener(
  "error",
  (event) => {
    const img = event.target;
    if (!(img instanceof HTMLImageElement)) {
      return;
    }
    const block = img.closest("#click-output .frame-block");
    if (block) {
      block.setAttribute("data-frame-missing", "");
    }
  },
  true
);

// Ring the Details point on the transect, T-S and scatter plots. The ring is
// a plain HTML element positioned over each plot; Plotly's layout is never
// touched. (Drawing it as a layout shape broke box/lasso selection: Plotly
// keeps selections and shapes in the same layer.) It is repositioned after
// every redraw and zoom/pan, and hidden when the point is out of view.
(() => {
  const GRAPH_IDS = ["main_plot", "ts_plot", "scatter_plot"];
  let currentId = null;
  let visible = true;
  let color = "#e00000";

  const findPoint = (gd, pointId) => {
    for (const trace of gd._fullData || []) {
      const customdata = trace.customdata;
      if (!customdata) {
        continue;
      }
      for (let i = 0; i < customdata.length; i += 1) {
        const value = customdata[i];
        const id = Array.isArray(value) ? value[0] : value;
        if (Number(id) === pointId) {
          return { x: trace.x[i], y: trace.y[i] };
        }
      }
    }
    return null;
  };

  const ringFor = (gd) => {
    let ring = gd.querySelector(":scope > .detail-ring");
    if (!ring) {
      ring = document.createElement("div");
      ring.className = "detail-ring";
      if (getComputedStyle(gd).position === "static") {
        gd.style.position = "relative";
      }
      gd.appendChild(ring);
    }
    return ring;
  };

  const placeRing = (gd) => {
    const ring = ringFor(gd);
    const layout = gd._fullLayout;
    const point = !visible || currentId === null ? null : findPoint(gd, currentId);
    if (!point || !layout || !layout.xaxis || !layout.yaxis) {
      ring.style.display = "none";
      return;
    }
    const xa = layout.xaxis;
    const ya = layout.yaxis;
    const px = xa._offset + xa.l2p(xa.d2l(point.x));
    const py = ya._offset + ya.l2p(ya.d2l(point.y));
    const inside =
      Number.isFinite(px) && Number.isFinite(py) &&
      px >= xa._offset && px <= xa._offset + xa._length &&
      py >= ya._offset && py <= ya._offset + ya._length;
    if (!inside) {
      ring.style.display = "none";
      return;
    }
    ring.style.borderColor = color;
    ring.style.left = `${px}px`;
    ring.style.top = `${py}px`;
    ring.style.display = "block";
  };

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    details: {
      markPoint(pointId, toggle, markerColor) {
        currentId = pointId === null || pointId === undefined ? null : Number(pointId);
        visible = Array.isArray(toggle) && toggle.length > 0;
        color = markerColor || "#e00000";
        for (const graphId of GRAPH_IDS) {
          const gd = document.querySelector(`#${graphId} .js-plotly-plot`);
          if (!gd || !gd._fullData) {
            continue;
          }
          if (!gd.__detailRingBound) {
            gd.on("plotly_afterplot", () => placeRing(gd));
            gd.on("plotly_relayout", () => placeRing(gd));
            gd.__detailRingBound = true;
          }
          placeRing(gd);
        }
        return window.dash_clientside.no_update;
      },
    },
  });
})();

// Drag the divider between the plots and Details to resize Details. The
// width is a CSS variable on <html>, remembered in this browser only;
// double-click the divider to go back to the default width.
(() => {
  const STORAGE_KEY = "stingray-details-width";
  const MIN_WIDTH = 260;
  const MAX_WIDTH = 700;
  const MIN_PLOT_WIDTH = 480;
  const root = document.documentElement;

  const setWidth = (px) => root.style.setProperty("--details-width", `${px}px`);

  try {
    const saved = Number(localStorage.getItem(STORAGE_KEY));
    if (saved >= MIN_WIDTH && saved <= MAX_WIDTH) {
      setWidth(saved);
    }
  } catch (e) {
    // Storage unavailable (private window, blocked site data): use the default.
  }

  let drag = null;

  document.addEventListener("mousedown", (event) => {
    const handle = event.target.closest && event.target.closest(".details-resizer");
    if (!handle || event.button !== 0) {
      return;
    }
    const body = handle.closest(".dashboard-body");
    if (!body) {
      return;
    }
    event.preventDefault();
    drag = { handle, body, width: null };
    handle.classList.add("dragging");
    document.body.classList.add("resizing-details");
  });

  document.addEventListener("mousemove", (event) => {
    if (!drag) {
      return;
    }
    const rect = drag.body.getBoundingClientRect();
    const maxWidth = Math.min(MAX_WIDTH, rect.width - MIN_PLOT_WIDTH);
    const width = Math.round(Math.max(MIN_WIDTH, Math.min(maxWidth, rect.right - event.clientX)));
    drag.width = width;
    setWidth(width);
  });

  document.addEventListener("mouseup", () => {
    if (!drag) {
      return;
    }
    drag.handle.classList.remove("dragging");
    document.body.classList.remove("resizing-details");
    if (drag.width !== null) {
      try {
        localStorage.setItem(STORAGE_KEY, String(drag.width));
      } catch (e) {
        // Not persisted; the width still applies for this page.
      }
    }
    drag = null;
  });

  document.addEventListener("dblclick", (event) => {
    if (!(event.target.closest && event.target.closest(".details-resizer"))) {
      return;
    }
    root.style.removeProperty("--details-width");
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch (e) {
      // Nothing stored to clear.
    }
  });
})();
