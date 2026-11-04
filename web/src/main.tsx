import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";
import { applyTheme, getWebApp } from "./telegram";

const webApp = getWebApp();
if (webApp) {
  webApp.ready();
  webApp.expand();
  applyTheme(webApp);
  webApp.onEvent("themeChanged", () => applyTheme(webApp));
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
