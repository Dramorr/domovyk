// Оформлення сторінок (data-deco на <html>): розділювачі перед небезпечними діями.
// Тема, а не роль. Зараз одна — «Містечко» (силует міста, townscape.tsx); «plain» лишається запасною (тонка лінія).

export type Deco = "plain" | "town";

export function applyDeco(d: Deco = "town"): void {
  document.documentElement.dataset.deco = d;
}
