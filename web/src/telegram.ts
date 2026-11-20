// Тонка типізована обгортка над window.Telegram.WebApp (лише те, що використовуємо).

interface WebApp {
  initData: string;
  initDataUnsafe: { start_param?: string };
  version: string;
  ready(): void;
  expand(): void;
  isVersionAtLeast(version: string): boolean;
  openLink(url: string): void;
  openTelegramLink?(url: string): void;
  setHeaderColor?(color: string): void;
  setBackgroundColor?(color: string): void;
  setBottomBarColor?(color: string): void;
  requestContact?(cb: (shared: boolean) => void): void;
  showConfirm?(message: string, cb: (ok: boolean) => void): void;
  downloadFile?(params: { url: string; file_name: string }, cb?: (accepted: boolean) => void): void;
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

/** Завантажити файл на пристрій. У Telegram (Bot API 8.0+) — його власне вікно «Зберегти файл»;
 *  у звичайному браузері — посилання з Content-Disposition: attachment.
 *  false — старий Telegram без downloadFile: хай викликач пропонує інший шлях (файл у чат). */
export function downloadFile(app: WebApp | null, url: string, fileName: string): boolean {
  if (app) {
    if (!app.downloadFile || !app.isVersionAtLeast("8.0")) return false;
    app.downloadFile({ url, file_name: fileName });
    return true;
  }
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  return true;
}

/** Відкрити t.me-посилання (бот із deep link) усередині Telegram. */
export function openTelegramLink(app: WebApp | null, url: string): void {
  if (app?.openTelegramLink) app.openTelegramLink(url);
  else openLink(app, url);
}

/** Своя світла тема Домовика: фарбуємо панелі Telegram у фон застосунку (не навпаки). */
export function applyBrandChrome(app: WebApp): void {
  if (!app.isVersionAtLeast("6.1")) return;
  app.setHeaderColor?.(BRAND_BG);
  app.setBackgroundColor?.(BRAND_BG);
  if (app.isVersionAtLeast("7.10")) app.setBottomBarColor?.("#ffffff");
}

const BRAND_BG = "#f6f4ee";

export type { WebApp };
