import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";
import { applyBrandChrome, getWebApp } from "./telegram";

const webApp = getWebApp();
if (webApp) {
  webApp.ready();
  webApp.expand();
  applyBrandChrome(webApp);
}

// Клавіатура телефона піднімає все, що закріплено внизу (меню, кнопку кроку, маскота).
// Поки курсор у текстовому полі — позначаємо це класом, CSS ховає закріплене.
const TYPING = "input:not([type]), input[type=text], input[type=search], input[type=number], input[type=tel], input[type=email], input[type=url], textarea";
const typing = (el: EventTarget | null) => el instanceof HTMLElement && (el.matches(TYPING) || el.isContentEditable);
document.addEventListener("focusin", (e) => {
  if (typing(e.target)) document.documentElement.classList.add("kb-open");
});
document.addEventListener("focusout", (e) => {
  if (typing(e.target)) document.documentElement.classList.remove("kb-open");
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
