// Оформлення сторінок (data-deco на <html>): розділювачі перед небезпечними діями.
// Тема, а не роль. Зараз одна — «Ліс»; «plain» лишається запасною (тонка лінія).

export type Deco = "plain" | "forest";

export function applyDeco(d: Deco = "forest"): void {
  document.documentElement.dataset.deco = d;
}
