import type { ReactNode } from "react";

import { ApiFailure } from "./api/client";
import { t, type TextKey } from "./i18n";

export const BRAND = `${import.meta.env.BASE_URL}../brand/`;

export function Header({ onBack }: { onBack?: () => void }) {
  return (
    <header className="header">
      {onBack ? (
        <button className="link" onClick={onBack}>
          {t("back")}
        </button>
      ) : (
        <img src={`${BRAND}app-icon.svg`} alt="" width={32} height={32} />
      )}
      <span className="brand">Домовик</span>
      <span className="badge">{t("beta")}</span>
    </header>
  );
}

export function Mascot({ lantern = false }: { lantern?: boolean }) {
  return (
    <img
      className="mascot"
      src={`${BRAND}${lantern ? "mascot-lantern" : "mascot"}.svg`}
      alt=""
    />
  );
}

export function Screen({ children, onBack }: { children: ReactNode; onBack?: () => void }) {
  return (
    <main className="screen">
      <Header onBack={onBack} />
      {children}
    </main>
  );
}

/** Текст для помилки API: спершу за доменним кодом, інакше — за типом. */
export function errorText(e: unknown): string {
  if (e instanceof ApiFailure) {
    if (e.kind === "domain") return t("error_domain");
    return t(`error_${e.kind}` as TextKey);
  }
  return t("error_network");
}
