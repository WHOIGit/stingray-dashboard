// Reset button (the refresh button): put every plot back to its starting
// view. Clicking each graph's own Home button reuses Plotly's reset, which also
// triggers the dashboard's redraw for the transect plot. The cruise-track
// selection box and highlighted points are cleared too.
(function () {
  const GRAPH_IDS = ["cruise_track", "main_plot", "ts_plot", "scatter_plot", "profile_plot"];

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    resetView: {
      resetAll(nClicks) {
        if (!nClicks) {
          return window.dash_clientside.no_update;
        }
        for (const graphId of GRAPH_IDS) {
          const gd = document.querySelector(`#${graphId} .js-plotly-plot`);
          if (!gd) {
            continue;
          }
          const home = gd.querySelector('.modebar-btn[data-val="reset"]');
          if (home) {
            home.click();
          }
        }
        const track = document.querySelector("#cruise_track .js-plotly-plot");
        if (track && window.Plotly) {
          window.Plotly.relayout(track, { selections: [] });
          window.Plotly.restyle(track, { selectedpoints: [null] });
        }
        return window.dash_clientside.no_update;
      },
    },
  });
})();
