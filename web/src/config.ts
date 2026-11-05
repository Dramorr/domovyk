// Публічні налаштування збірки. Секретів тут не буває: усе це бачать усі.

const RAW_BOT = ((import.meta.env.VITE_BOT_USERNAME as string | undefined) ?? "").replace(/^@/, "");
// Нік бота Telegram: 5–32 символи [A-Za-z0-9_]. Заглушка TODO не пройде.
export const BOT_USERNAME = /^[A-Za-z0-9_]{5,32}$/.test(RAW_BOT) && RAW_BOT !== "TODO" ? RAW_BOT : "";

/** Посилання на бота з deep link payload (BOT_FLOWS §0.4). */
export function botLink(start?: string): string {
  if (!BOT_USERNAME) return "";
  return `https://t.me/${BOT_USERNAME}${start ? `?start=${encodeURIComponent(start)}` : ""}`;
}

/** Послуга в Дії: інформаційна довідка з реєстру речових прав (PRP-OWN, OWN-1). */
export const DIIA_URL = "https://diia.gov.ua/services/informaciya-z-derzhavnogo-reyestru-rechovih-prav-na-neruhome-majno";

/** Бета працює лише у Львові (значення для API, не текст інтерфейсу). */
export const CITY = "Львів";
