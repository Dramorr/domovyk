// Оформлення сторінок (data-deco на <html>): розділювачі перед небезпечними діями.
// Не прив'язане до ролі — це тема, яку обирають у профілі. Зберігається лише в цьому браузері.

export type Deco = "plain" | "forest";

export const DECOS: Deco[] = ["plain", "forest"];
const STORE = "domovyk:deco";
const DEFAULT: Deco = "forest";

export function getDeco(): Deco {
  try {
    const v = localStorage.getItem(STORE);
    return DECOS.includes(v as Deco) ? (v as Deco) : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

export function applyDeco(d: Deco): void {
  document.documentElement.dataset.deco = d;
}

export function setDeco(d: Deco): void {
  applyDeco(d);
  try {
    localStorage.setItem(STORE, d);
  } catch {
    // приватне вікно — оформлення просто не запам'ятається
  }
}
