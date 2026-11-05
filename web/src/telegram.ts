// Тонка типізована обгортка над window.Telegram.WebApp (лише те, що використовуємо).

type ThemeParams = Partial<
  Record<
    | "bg_color"
    | "text_color"
    | "hint_color"
    | "link_color"
    | "button_color"
    | "button_text_color"
    | "secondary_bg_color",
    string
  >
>;

interface WebApp {
  initData: string;
  initDataUnsafe: { start_param?: string };
  colorScheme: "light" | "dark";
  themeParams: ThemeParams;
  version: string;
  ready(): void;
  expand(): void;
  isVersionAtLeast(version: string): boolean;
  onEvent(event: "themeChanged", cb: () => void): void;
  openLink(url: string): void;
  openTelegramLink?(url: string): void;
  requestContact?(cb: (shared: boolean) => void): void;
  showConfirm?(message: string, cb: (ok: boolean) => void): void;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: WebApp };
  }
}

export function getWebApp(): WebApp | null {
  const app = window.Telegram?.WebApp;
  // Поза Telegram скрипт теж створює об'єкт, але initData порожній.
  return app && app.initData ? app : null;
}

export function startParam(app: WebApp | null): string | null {
  return app?.initDataUnsafe.start_param ?? new URLSearchParams(location.search).get("start");
}

/** Поділитися номером (FR-TMA-5). Номер прийде боту повідомленням-контактом від Telegram. */
export function requestContact(app: WebApp): Promise<boolean> {
  return new Promise((resolve) => {
    if (!app.requestContact || !app.isVersionAtLeast("6.9")) {
      resolve(false);
      return;
    }
    app.requestContact((shared) => resolve(shared));
  });
}

export function confirm(app: WebApp | null, message: string): Promise<boolean> {
  return new Promise((resolve) => {
    if (app?.showConfirm && app.isVersionAtLeast("6.2")) app.showConfirm(message, resolve);
    else resolve(window.confirm(message));
  });
}

export function openLink(app: WebApp | null, url: string): void {
  if (app) app.openLink(url);
  else window.open(url, "_blank", "noopener");
}

/** Відкрити t.me-посилання (бот із deep link) усередині Telegram. */
export function openTelegramLink(app: WebApp | null, url: string): void {
  if (app?.openTelegramLink) app.openTelegramLink(url);
  else openLink(app, url);
}

const THEME_VARS: Record<keyof ThemeParams, string> = {
  bg_color: "--tg-bg",
  text_color: "--tg-text",
  hint_color: "--tg-hint",
  link_color: "--tg-link",
  button_color: "--tg-button",
  button_text_color: "--tg-button-text",
  secondary_bg_color: "--tg-surface",
};

export function applyTheme(app: WebApp): void {
  const root = document.documentElement;
  root.dataset.theme = app.colorScheme;
  for (const [key, cssVar] of Object.entries(THEME_VARS)) {
    const value = app.themeParams[key as keyof ThemeParams];
    if (value) root.style.setProperty(cssVar, value);
  }
}

export type { WebApp };
