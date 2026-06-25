(function() {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) {
    console.warn("[trade-vision] Plugin SDK not available");
    return;
  }

  const React = SDK.React;
  const { useState, useEffect } = SDK.hooks;

  function TradeVisionPanel() {
    const [height, setHeight] = React.useState(2400);

    React.useEffect(() => {
      function onMessage(e) {
        if (e.data && e.data.type === "trade-vision-resize" && typeof e.data.height === "number") {
          setHeight(e.data.height);
        }
      }
      window.addEventListener("message", onMessage);
      return () => window.removeEventListener("message", onMessage);
    }, []);

    return React.createElement(
      "div",
      { style: { width: "100%", maxWidth: "1400px", margin: "0 auto", padding: "0 16px" } },
      React.createElement(
        "div",
        {
          style: {
            display: "flex",
            alignItems: "center",
            gap: "12px",
            padding: "12px 0",
            borderBottom: "1px solid #30363d",
            marginBottom: "12px",
          },
        },
        React.createElement(
          "h1",
          { style: { margin: 0, fontSize: "20px", color: "#e6edf3" } },
          "📈 Trade Vision"
        ),
        React.createElement(
          "span",
          { style: { color: "#8b949e", fontSize: "13px" } },
          "Short-dated covered-call advisor — daily 4:30pm ET pipeline"
        ),
        React.createElement(
          "a",
          {
            href: "/dashboard-plugins/trade-vision/panel.html",
            target: "_blank",
            style: {
              marginLeft: "auto",
              padding: "4px 10px",
              borderRadius: "6px",
              background: "#1f242c",
              color: "#58a6ff",
              textDecoration: "none",
              fontSize: "12px",
              border: "1px solid #30363d",
            },
          },
          "Open in new tab ↗"
        )
      ),
      React.createElement("iframe", {
        src: "/dashboard-plugins/trade-vision/panel.html",
        style: {
          width: "100%",
          height: height + "px",
          border: "none",
          background: "#0d1117",
          borderRadius: "8px",
        },
        title: "Trade Vision",
      })
    );
  }

  window.__HERMES_PLUGINS__.register("trade-vision", TradeVisionPanel);
  console.log("[trade-vision] Plugin registered");
})();
