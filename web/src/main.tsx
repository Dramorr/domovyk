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
const root = document.documentElement;
let closing: number | undefined;
document.addEventListener("focusin", (e) => {
  if (!typing(e.target)) return;
  window.clearTimeout(closing);
  root.classList.add("kb-open");
});
document.addEventListener("focusout", (e) => {
  if (!typing(e.target)) return;
  // Не одразу: натиск на кнопку спершу забирає фокус з поля, і якби меню й кнопка кроку
  // миттєво стали на місце, кнопка втекла б з-під пальця (натиск не спрацьовував).
  window.clearTimeout(closing);
  closing = window.setTimeout(() => {
    if (!typing(document.activeElement)) root.classList.remove("kb-open");
  }, 350);
});
// Клавіатуру сховали, а курсор лишився в полі (кнопка «Назад» на Android): екран знову повної висоти.
const vv = window.visualViewport;
if (vv) {
  let full = vv.height;
  let shrunk = false;
  vv.addEventListener("resize", () => {
    if (vv.height > full) full = vv.height;
    if (vv.height < full - 120) {
      shrunk = true;
      // Клавіатуру відкрили знову в тому самому полі (без нового focusin).
      if (typing(document.activeElement)) root.classList.add("kb-open");
    } else if (shrunk && root.classList.contains("kb-open")) {
      shrunk = false;
      root.classList.remove("kb-open");
    }
  });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
