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
  ready(): void;
  expand(): void;
  onEvent(event: "themeChanged", cb: () => void): void;
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
