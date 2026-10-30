// Figure export: a scaled copy of the plot as it looks on screen, at a width in
// inches and a resolution in dpi, like exporting an R plot to a file. Height, text,
// markers, lines and the color bar all scale with the width, so there is
// nothing else to set. PNG and TIFF go through the server (Pillow) so the dpi is
// stored in the file; SVG is written directly with its size in inches.
(function () {
  const CSS_DPI = 96; // Plotly lays out in CSS pixels
  const MAX_SIDE = 16000; // browsers refuse canvases much larger than this

  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

  const saveBlob = (blob, filename) => {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  };

  const dataUrlToBlob = async (url) => (await fetch(url)).blob();

  const svgWithPhysicalSize = (svgText, widthIn, heightIn) =>
    svgText.replace(/<svg([^>]*?)>/, (match, attrs) => {
      const cleaned = attrs
        .replace(/\swidth="[^"]*"/, "")
        .replace(/\sheight="[^"]*"/, "");
      return `<svg${cleaned} width="${widthIn}in" height="${heightIn}in">`;
    });

  const cloneLayout = (layout) =>
    typeof structuredClone === "function" ? structuredClone(layout) : JSON.parse(JSON.stringify(layout));

  // WebGL traces (scattergl) are drawn at screen resolution and then enlarged, so
  // their markers turn blocky at print dpi, and SVG export embeds that raster.
  // Drawing them as ordinary (vector) scatter traces keeps them sharp at any
  // size. Very large data sets stay on WebGL to keep the export feasible.
  const MAX_VECTOR_POINTS = 250000;
  const vectorTraces = (traces) => {
    const points = traces.reduce(
      (sum, trace) => sum + (trace.x && trace.x.length ? trace.x.length : 0),
      0
    );
    if (points > MAX_VECTOR_POINTS) {
      return { data: traces, converted: false, points };
    }
    return {
      data: traces.map((trace) =>
        trace.type === "scattergl" ? Object.assign({}, trace, { type: "scatter" }) : trace
      ),
      converted: true,
      points,
    };
  };

  async function exportFigure(nClicks, target, format, widthIn, dpi, background, multi) {
    const noUpdate = window.dash_clientside.no_update;
    if (!nClicks || !target) {
      return noUpdate;
    }
    const status = document.getElementById("export-status");
    const say = (text) => {
      if (status) {
        status.textContent = text;
      }
    };
    if (document.querySelector("#export-panel-rows .export-panel-row")) {
      // Panels: the server builds them, then composePanels() saves or combines them.
      say("Building panels…");
      return noUpdate;
    }
    try {
      const gd = document.querySelector(`#${target} .js-plotly-plot`);
      if (!gd || !gd.data || !window.Plotly) {
        say("Nothing to export yet.");
        return noUpdate;
      }
      const fullLayout = gd._fullLayout || {};
      const screenWidth = fullLayout.width || gd.offsetWidth || 1;
      const screenHeight = fullLayout.height || gd.offsetHeight || 1;

      const widthInValue = Number(widthIn) > 0 ? Number(widthIn) : 11;
      const heightInValue = Number(((widthInValue * screenHeight) / screenWidth).toFixed(2));
      const dpiValue = clamp(Number(dpi) || 300, 72, 1200);
      // Pixels of the file per CSS pixel of the on-screen figure.
      const scale = (widthInValue * dpiValue) / screenWidth;

      const layout = Object.assign(cloneLayout(gd.layout), {
        width: screenWidth,
        height: screenHeight,
        autosize: false,
      });
      if (background === "transparent") {
        layout.paper_bgcolor = "rgba(0,0,0,0)";
        layout.plot_bgcolor = "rgba(0,0,0,0)";
      }
      const vector = vectorTraces(gd.data);
      const figure = { data: vector.data, layout };
      const rasterNote = vector.converted
        ? ""
        : ` Note: ${vector.points.toLocaleString()} points, so markers stay raster (WebGL); use a larger bin size for sharp markers.`;

      const options = (gd._context && gd._context.toImageButtonOptions) || {};
      const base = options.filename || "stingray_plot";

      if (format === "svg") {
        say("Rendering SVG…");
        const url = await window.Plotly.toImage(figure, { format: "svg", width: screenWidth, height: screenHeight });
        const svgText = decodeURIComponent(url.slice(url.indexOf(",") + 1));
        const sized = svgWithPhysicalSize(svgText, widthInValue, heightInValue);
        saveBlob(new Blob([sized], { type: "image/svg+xml" }), `${base}.svg`);
        say(`Saved ${widthInValue} × ${heightInValue} in SVG.${rasterNote}`);
        return noUpdate;
      }

      const pixelsWide = Math.round(screenWidth * scale);
      const pixelsHigh = Math.round(screenHeight * scale);
      if (pixelsWide > MAX_SIDE || pixelsHigh > MAX_SIDE) {
        say("Too large for the browser: lower the width or dpi.");
        return noUpdate;
      }
      say("Rendering…");
      const pngUrl = await window.Plotly.toImage(figure, {
        format: "png",
        width: screenWidth,
        height: screenHeight,
        scale,
      });
      const pngBlob = await dataUrlToBlob(pngUrl);
      const kind = format === "tiff" ? "tiff" : "png";
      const response = await fetch(`/export/image?format=${kind}&dpi=${dpiValue}`, {
        method: "POST",
        headers: { "Content-Type": "image/png" },
        body: pngBlob,
      });
      if (!response.ok) {
        say(`Export failed (${response.status}). ${await response.text().then((t) => t.slice(0, 120))}`);
        return noUpdate;
      }
      saveBlob(await response.blob(), `${base}.${kind === "tiff" ? "tif" : "png"}`);
      say(
        `Saved ${pixelsWide} × ${pixelsHigh} px ${kind.toUpperCase()} ` +
          `(${widthInValue} × ${heightInValue} in at ${dpiValue} dpi).${rasterNote}`
      );
    } catch (error) {
      say(`Export failed: ${error && error.message ? error.message : error}`);
    }
    return noUpdate;
  }

  // Each panel saved as its own file, like a single-plot export.
  async function saveSeparately(figures, names, format, widthIn, dpi, background, target, say) {
    const noUpdate = window.dash_clientside.no_update;
    if (!window.Plotly) {
      return noUpdate;
    }
    try {
      const gd = document.querySelector(`#${target} .js-plotly-plot`);
      const options = (gd && gd._context && gd._context.toImageButtonOptions) || {};
      const base = options.filename || "stingray_plot";
      const widthInValue = Number(widthIn) > 0 ? Number(widthIn) : 11;
      const dpiValue = clamp(Number(dpi) || 300, 72, 1200);
      const kind = format === "tiff" ? "tiff" : "png";
      for (let index = 0; index < figures.length; index++) {
        say(`Rendering panel ${index + 1} of ${figures.length}…`);
        const source = figures[index];
        const width = (source.layout && source.layout.width) || 600;
        const height = (source.layout && source.layout.height) || 450;
        const layout = Object.assign({}, source.layout, { autosize: false });
        if (background === "transparent") {
          layout.paper_bgcolor = "rgba(0,0,0,0)";
          layout.plot_bgcolor = "rgba(0,0,0,0)";
        }
        const figure = {
          data: (source.data || []).map((trace) =>
            trace.type === "scattergl" ? Object.assign({}, trace, { type: "scatter" }) : trace
          ),
          layout,
        };
        const name = names[index] || `${base}_panel${index + 1}`;
        const heightInValue = Number(((widthInValue * height) / width).toFixed(2));
        if (format === "svg") {
          const url = await window.Plotly.toImage(figure, { format: "svg", width, height });
          const svgText = decodeURIComponent(url.slice(url.indexOf(",") + 1));
          saveBlob(
            new Blob([svgWithPhysicalSize(svgText, widthInValue, heightInValue)], { type: "image/svg+xml" }),
            `${name}.svg`
          );
          continue;
        }
        const scale = (widthInValue * dpiValue) / width;
        if (Math.round(width * scale) > MAX_SIDE || Math.round(height * scale) > MAX_SIDE) {
          say("Too large for the browser: lower the width or dpi.");
          return noUpdate;
        }
        const pngUrl = await window.Plotly.toImage(figure, { format: "png", width, height, scale });
        const response = await fetch(`/export/image?format=${kind}&dpi=${dpiValue}`, {
          method: "POST",
          headers: { "Content-Type": "image/png" },
          body: await dataUrlToBlob(pngUrl),
        });
        if (!response.ok) {
          say(`Export failed (${response.status}). ${await response.text().then((t) => t.slice(0, 120))}`);
          return noUpdate;
        }
        saveBlob(await response.blob(), `${name}.${kind === "tiff" ? "tif" : "png"}`);
      }
      say(`Saved ${figures.length} separate ${format.toUpperCase()} files, ${widthInValue} in wide.`);
    } catch (error) {
      say(`Export failed: ${error && error.message ? error.message : error}`);
    }
    return noUpdate;
  }

  // Several panels in one figure: each panel is a figure built by the server for
  // one set of variables; they are laid out in a grid and written as one image.
  async function composePanels(store, format, widthIn, dpi, background, rowsInput, colsInput, letters, target, mode, pageWidth, pageHeight, margin) {
    const noUpdate = window.dash_clientside.no_update;
    if (!store) {
      return noUpdate;
    }
    const status = document.getElementById("export-status");
    const say = (text) => {
      if (status) {
        status.textContent = text;
      }
    };
    if (store.error) {
      say(store.error);
      return noUpdate;
    }
    const figures = store.figures || [];
    const count = figures.length;
    if (!count || !window.Plotly) {
      return noUpdate;
    }
    if (!(mode && mode.includes("on")) || count < 2) {
      return saveSeparately(figures, store.names || [], format, widthIn, dpi, background, target, say);
    }
    try {
      let cols = Number(colsInput) > 0 ? Math.floor(Number(colsInput)) : 0;
      let rows = Number(rowsInput) > 0 ? Math.floor(Number(rowsInput)) : 0;
      if (!cols && !rows) {
        cols = Math.ceil(Math.sqrt(count));
        rows = Math.ceil(count / cols);
      } else if (!cols) {
        cols = Math.ceil(count / rows);
      } else if (!rows) {
        rows = Math.ceil(count / cols);
      }
      if (rows * cols < count) {
        say(`${rows} × ${cols} cells cannot hold ${count} panels.`);
        return noUpdate;
      }

      const GAP = 12; // CSS px between panels
      const sizes = figures.map((figure) => ({
        w: (figure.layout && figure.layout.width) || 600,
        h: (figure.layout && figure.layout.height) || 450,
      }));
      const colWidth = new Array(cols).fill(0);
      const rowHeight = new Array(rows).fill(0);
      sizes.forEach((size, index) => {
        const row = Math.floor(index / cols);
        const col = index % cols;
        colWidth[col] = Math.max(colWidth[col], size.w);
        rowHeight[row] = Math.max(rowHeight[row], size.h);
      });
      const colX = [];
      const rowY = [];
      let x = 0;
      colWidth.forEach((width) => { colX.push(x); x += width + GAP; });
      let y = 0;
      rowHeight.forEach((height) => { rowY.push(y); y += height + GAP; });
      const totalWidth = x - GAP;
      const totalHeight = y - GAP;

      const dpiValue = clamp(Number(dpi) || 300, 72, 1200);
      let widthInValue = Number(widthIn) > 0 ? Number(widthIn) : 11;
      let heightInValue = Number(((widthInValue * totalHeight) / totalWidth).toFixed(2));
      // The file is the grid, or, with a page set, the whole page with the grid
      // scaled to fit inside the margins and centered.
      let canvasWidth = totalWidth;
      let canvasHeight = totalHeight;
      let offX = 0;
      let offY = 0;
      let fitNote = "";
      const pageW = Number(pageWidth);
      const pageH = Number(pageHeight);
      if (pageW > 0 && pageH > 0) {
        const marginIn = Number(margin) >= 0 ? Number(margin) : 0.5;
        const availWidth = pageW - 2 * marginIn;
        const availHeight = pageH - 2 * marginIn;
        if (availWidth <= 0 || availHeight <= 0) {
          say("The margin is larger than the page.");
          return noUpdate;
        }
        const inchesPerPixel = Math.min(availWidth / totalWidth, availHeight / totalHeight);
        canvasWidth = pageW / inchesPerPixel;
        canvasHeight = pageH / inchesPerPixel;
        offX = (canvasWidth - totalWidth) / 2;
        offY = (canvasHeight - totalHeight) / 2;
        widthInValue = pageW;
        heightInValue = pageH;
        const font = figures[0].layout && figures[0].layout.font;
        const points = ((font && font.size) || 14) * inchesPerPixel * 72;
        fitNote = ` Page ${pageW} × ${pageH} in, text ≈ ${points.toFixed(1)} pt.`;
      }
      const scale = (widthInValue * dpiValue) / canvasWidth;
      const withLetters = letters === "lower" || letters === "upper";
      const letterOf = (index) =>
        `(${String.fromCharCode((letters === "lower" ? 97 : 65) + (index % 26))})`;
      // Letters are a little larger than the panel's own text.
      const letterSize = (index) => {
        const font = figures[index].layout && figures[index].layout.font;
        return ((font && font.size) || 14) + 2;
      };

      const panelFigure = (figure) => {
        const layout = Object.assign({}, figure.layout, { autosize: false });
        if (background === "transparent") {
          layout.paper_bgcolor = "rgba(0,0,0,0)";
          layout.plot_bgcolor = "rgba(0,0,0,0)";
        }
        return {
          data: (figure.data || []).map((trace) =>
            trace.type === "scattergl" ? Object.assign({}, trace, { type: "scatter" }) : trace
          ),
          layout,
        };
      };

      const gd = document.querySelector(`#${target} .js-plotly-plot`);
      const options = (gd && gd._context && gd._context.toImageButtonOptions) || {};
      const base = `${options.filename || "stingray_plot"}_panels`;

      if (format === "svg") {
        const parts = [];
        for (let index = 0; index < count; index++) {
          say(`Rendering panel ${index + 1} of ${count}…`);
          const row = Math.floor(index / cols);
          const col = index % cols;
          const url = await window.Plotly.toImage(panelFigure(figures[index]), {
            format: "svg", width: sizes[index].w, height: sizes[index].h,
          });
          const svgText = decodeURIComponent(url.slice(url.indexOf(",") + 1));
          parts.push(svgText.replace(/<svg([^>]*?)>/, (match, attrs) => {
            const cleaned = attrs.replace(/\s(?:width|height|x|y)="[^"]*"/g, "");
            return `<svg${cleaned} x="${colX[col] + offX}" y="${rowY[row] + offY}" width="${sizes[index].w}" height="${sizes[index].h}">`;
          }));
          if (withLetters) {
            parts.push(
              `<text x="${colX[col] + offX + 4}" y="${rowY[row] + offY + letterSize(index) + 2}" font-family="sans-serif" ` +
              `font-size="${letterSize(index)}" font-weight="bold">${letterOf(index)}</text>`
            );
          }
        }
        const backdrop = background === "transparent" ? "" : '<rect width="100%" height="100%" fill="#fff"/>';
        const svg =
          `<svg xmlns="http://www.w3.org/2000/svg" width="${widthInValue}in" height="${heightInValue}in" ` +
          `viewBox="0 0 ${canvasWidth} ${canvasHeight}">${backdrop}${parts.join("")}</svg>`;
        saveBlob(new Blob([svg], { type: "image/svg+xml" }), `${base}.svg`);
        say(`Saved ${count} panels, ${widthInValue} × ${heightInValue} in SVG.${fitNote}`);
        return noUpdate;
      }

      const pixelsWide = Math.round(canvasWidth * scale);
      const pixelsHigh = Math.round(canvasHeight * scale);
      if (pixelsWide > MAX_SIDE || pixelsHigh > MAX_SIDE) {
        say("Too large for the browser: lower the width or dpi.");
        return noUpdate;
      }
      const canvas = document.createElement("canvas");
      canvas.width = pixelsWide;
      canvas.height = pixelsHigh;
      const context = canvas.getContext("2d");
      if (background !== "transparent") {
        context.fillStyle = "#ffffff";
        context.fillRect(0, 0, pixelsWide, pixelsHigh);
      }
      for (let index = 0; index < count; index++) {
        say(`Rendering panel ${index + 1} of ${count}…`);
        const row = Math.floor(index / cols);
        const col = index % cols;
        const url = await window.Plotly.toImage(panelFigure(figures[index]), {
          format: "png", width: sizes[index].w, height: sizes[index].h, scale,
        });
        const image = await new Promise((resolve, reject) => {
          const element = new Image();
          element.onload = () => resolve(element);
          element.onerror = reject;
          element.src = url;
        });
        context.drawImage(image, Math.round((colX[col] + offX) * scale), Math.round((rowY[row] + offY) * scale));
        if (withLetters) {
          context.fillStyle = "#000000";
          context.font = `bold ${Math.round(letterSize(index) * scale)}px sans-serif`;
          context.textBaseline = "alphabetic";
          context.fillText(letterOf(index), Math.round((colX[col] + offX + 4) * scale), Math.round((rowY[row] + offY + letterSize(index) + 2) * scale));
        }
      }
      say("Encoding…");
      const pngBlob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      const kind = format === "tiff" ? "tiff" : "png";
      const response = await fetch(`/export/image?format=${kind}&dpi=${dpiValue}`, {
        method: "POST",
        headers: { "Content-Type": "image/png" },
        body: pngBlob,
      });
      if (!response.ok) {
        say(`Export failed (${response.status}). ${await response.text().then((t) => t.slice(0, 120))}`);
        return noUpdate;
      }
      saveBlob(await response.blob(), `${base}.${kind === "tiff" ? "tif" : "png"}`);
      say(
        `Saved ${count} panels, ${pixelsWide} × ${pixelsHigh} px ${kind.toUpperCase()} ` +
          `(${widthInValue} × ${heightInValue} in at ${dpiValue} dpi).${fitNote}`
      );
    } catch (error) {
      say(`Export failed: ${error && error.message ? error.message : error}`);
    }
    return noUpdate;
  }

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    exportFigure: { exportFigure, composePanels },
  });
})();
