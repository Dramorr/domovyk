import { useCallback, useEffect, useState } from "react";

import { api, ApiFailure, type ApiError, type Me } from "./api/client";
import { t } from "./i18n";
import { getWebApp } from "./telegram";

const BOT_USERNAME = import.meta.env.VITE_BOT_USERNAME as string | undefined;
const BRAND = `${import.meta.env.BASE_URL}../brand/`;

type State = { kind: "loading" } | { kind: "ok"; me: Me } | { kind: "error"; error: ApiError };

function Header() {
  return (
    <header className="header">
      <img src={`${BRAND}app-icon.svg`} alt="" width={32} height={32} />
      <span className="brand">Домовик</span>
      <span className="badge">{t("beta")}</span>
    </header>
  );
}

export function App() {
  const webApp = getWebApp();
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(() => {
    if (!webApp) return;
    setState({ kind: "loading" });
    api
      .me(webApp.initData)
      .then((me) => setState({ kind: "ok", me }))
      .catch((e: unknown) =>
        setState({ kind: "error", error: e instanceof ApiFailure ? e.kind : "network" }),
      );
  }, [webApp]);

  useEffect(load, [load]);

  if (!webApp) {
    return (
      <main className="screen">
        <Header />
        <img className="mascot" src={`${BRAND}mascot.svg`} alt="" />
        <p>{t("open_in_telegram")}</p>
        {BOT_USERNAME && (
          <a className="button" href={`https://t.me/${BOT_USERNAME}`}>
            {t("open_bot")}
          </a>
        )}
      </main>
    );
  }

  return (
    <main className="screen">
      <Header />
      {state.kind === "loading" && <p className="hint">{t("loading")}</p>}
      {state.kind === "ok" && (
        <>
          <img className="mascot" src={`${BRAND}mascot.svg`} alt="" />
          <h1>{t("hello", { name: state.me.first_name })}</h1>
          <p>{t("tagline")}</p>
          <p className="hint">{t("soon")}</p>
        </>
      )}
      {state.kind === "error" && (
        <>
          <img className="mascot" src={`${BRAND}mascot-lantern.svg`} alt="" />
          <p>{t(`error_${state.error}`)}</p>
          {state.error !== "no_api" && (
            <button className="button" onClick={load}>
              {t("retry")}
            </button>
          )}
        </>
      )}
    </main>
  );
}
