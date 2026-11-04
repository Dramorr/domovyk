import { useCallback, useEffect, useState } from "react";

import { api, setInitData, type Me, type User } from "./api/client";
import { t } from "./i18n";
import { ConsentAndName, PhoneStep } from "./screens/Onboarding";
import { Profile } from "./screens/Profile";
import { getWebApp } from "./telegram";
import { errorText, Mascot, Screen } from "./ui";

const RAW_BOT = ((import.meta.env.VITE_BOT_USERNAME as string | undefined) ?? "").replace(/^@/, "");
// Нік бота Telegram: 5–32 символи [A-Za-z0-9_]. Заглушка TODO не пройде.
const BOT_USERNAME = /^[A-Za-z0-9_]{5,32}$/.test(RAW_BOT) && RAW_BOT !== "TODO" ? RAW_BOT : "";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string; retry: boolean }
  | { kind: "ready"; me: Me }
  | { kind: "deleted" };

export function App() {
  const webApp = getWebApp();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [screen, setScreen] = useState<"home" | "profile">("home");

  const load = useCallback(() => {
    if (!webApp) return;
    setInitData(webApp.initData);
    setState({ kind: "loading" });
    api
      .me()
      .then((me) => setState({ kind: "ready", me }))
      .catch((e: unknown) => setState({ kind: "error", message: errorText(e), retry: true }));
  }, [webApp]);

  useEffect(load, [load]);

  if (!webApp) {
    return (
      <Screen>
        <Mascot />
        <p>{t("open_in_telegram")}</p>
        {BOT_USERNAME && (
          <a className="button" href={`https://t.me/${BOT_USERNAME}`}>
            {t("open_bot")}
          </a>
        )}
      </Screen>
    );
  }

  if (state.kind === "loading") {
    return (
      <Screen>
        <p className="hint">{t("loading")}</p>
      </Screen>
    );
  }
  if (state.kind === "error") {
    return (
      <Screen>
        <Mascot lantern />
        <p>{state.message}</p>
        {state.retry && (
          <button className="button" onClick={load}>
            {t("retry")}
          </button>
        )}
      </Screen>
    );
  }
  if (state.kind === "deleted") {
    return (
      <Screen>
        <Mascot lantern />
        <p>{t("prof_deleted")}</p>
      </Screen>
    );
  }

  const { me } = state;
  const setUser = (user: User) => setState({ kind: "ready", me: { ...me, user } });
  const user = me.user;

  if (!user || user.status === "deleted") {
    return <ConsentAndName me={me} webApp={webApp} onRegistered={setUser} />;
  }
  if (user.status === "banned") {
    // Стани акаунта, що перехоплюють усе (BOT_FLOWS §0.5): спокійно, без маскота й жартів.
    return (
      <Screen>
        <h1>{t("banned_title")}</h1>
        <p>{t("banned_text")}</p>
      </Screen>
    );
  }
  if (!user.has_phone) {
    return <PhoneStep webApp={webApp} onDone={setUser} />;
  }

  if (screen === "profile") {
    return (
      <Profile
        user={user}
        webApp={webApp}
        onBack={() => setScreen("home")}
        onUserChange={setUser}
        onDeleted={() => setState({ kind: "deleted" })}
      />
    );
  }

  return (
    <Screen>
      <Mascot />
      <h1>{t("home_hello", { name: user.display_name })}</h1>
      {user.role !== "user" && <p className="role">{t(`role_${user.role}`)}</p>}
      <p>{t("home_tagline")}</p>
      <p className="hint">{t("home_soon")}</p>
      <button className="button" onClick={() => setScreen("profile")}>
        {t("home_profile")}
      </button>
    </Screen>
  );
}
