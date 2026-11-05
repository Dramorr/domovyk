import { useCallback, useEffect, useState } from "react";

import { api, setInitData, type Me, type User } from "./api/client";
import { BOT_USERNAME } from "./config";
import { t } from "./i18n";
import { ModerationCase, ModerationQueue } from "./screens/Moderation";
import { ConsentAndName, PhoneStep } from "./screens/Onboarding";
import { Profile } from "./screens/Profile";
import { PropertyProfile } from "./screens/PropertyProfile";
import { NewProperty, OwnershipScreen, PropertyCard, PropertyList } from "./screens/Properties";
import { getWebApp, startParam } from "./telegram";
import { errorText, Mascot, Screen } from "./ui";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string; retry: boolean }
  | { kind: "ready"; me: Me }
  | { kind: "deleted" };

type Route =
  | { name: "home" }
  | { name: "profile" }
  | { name: "properties" }
  | { name: "new-property" }
  | { name: "property"; id: string }
  | { name: "ownership"; id: string }
  | { name: "profile-form"; id: string }
  | { name: "moderation" }
  | { name: "mod-case"; vid: string };

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Екран із deep link (кнопки в сповіщеннях бота): prop_<id>, mod. */
function routeFromStart(param: string | null, isStaff: boolean): Route[] {
  if (!param) return [];
  if (param === "mod" && isStaff) return [{ name: "moderation" }];
  const id = param.startsWith("prop_") ? param.slice(5) : "";
  if (UUID_RE.test(id)) return [{ name: "properties" }, { name: "property", id }];
  return [];
}

export function App() {
  const webApp = getWebApp();
  const [state, setState] = useState<State>({ kind: "loading" });
  // Стек екранів: «Назад» повертає на попередній.
  const [stack, setStack] = useState<Route[]>([{ name: "home" }]);
  const [modCount, setModCount] = useState<number | null>(null);

  const route = stack[stack.length - 1];
  const push = (r: Route) => setStack((s) => [...s, r]);
  const back = () => setStack((s) => (s.length > 1 ? s.slice(0, -1) : s));
  const replace = (r: Route) => setStack((s) => [...s.slice(0, -1), r]);

  const load = useCallback(() => {
    if (!webApp) return;
    setInitData(webApp.initData);
    setState({ kind: "loading" });
    api
      .me()
      .then((me) => {
        setState({ kind: "ready", me });
        const staff = me.user?.role === "moderator" || me.user?.role === "admin";
        const deep = routeFromStart(startParam(webApp), staff);
        if (deep.length) setStack([{ name: "home" }, ...deep]);
      })
      .catch((e: unknown) => setState({ kind: "error", message: errorText(e), retry: true }));
  }, [webApp]);

  useEffect(load, [load]);

  const user = state.kind === "ready" ? state.me.user : null;
  const isStaff = user?.role === "moderator" || user?.role === "admin";
  const atHome = route.name === "home";

  useEffect(() => {
    if (!isStaff || !atHome) return;
    api
      .moderationSummary()
      .then((s) => setModCount(s.ownership_pending))
      .catch(() => setModCount(null));
  }, [isStaff, atHome]);

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
  const setUser = (u: User) => setState({ kind: "ready", me: { ...me, user: u } });

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

  switch (route.name) {
    case "profile":
      return (
        <Profile
          user={user}
          webApp={webApp}
          onBack={back}
          onUserChange={setUser}
          onDeleted={() => setState({ kind: "deleted" })}
        />
      );
    case "properties":
      return (
        <PropertyList
          onBack={back}
          onOpen={(id) => push({ name: "property", id })}
          onNew={() => push({ name: "new-property" })}
        />
      );
    case "new-property":
      return <NewProperty onBack={back} onCreated={(p) => replace({ name: "property", id: p.id })} />;
    case "property":
      return (
        <PropertyCard
          key={route.id}
          id={route.id}
          webApp={webApp}
          onBack={back}
          onOwnership={() => push({ name: "ownership", id: route.id })}
          onProfile={() => push({ name: "profile-form", id: route.id })}
          onDeleted={back}
        />
      );
    case "ownership":
      return <OwnershipScreen key={route.id} id={route.id} webApp={webApp} onBack={back} />;
    case "profile-form":
      return <PropertyProfile key={route.id} id={route.id} onBack={back} />;
    case "moderation":
      return <ModerationQueue onBack={back} onOpen={(vid) => push({ name: "mod-case", vid })} />;
    case "mod-case":
      return <ModerationCase key={route.vid} vid={route.vid} onBack={back} />;
    case "home":
      break;
  }

  return (
    <Screen>
      <Mascot />
      <h1>{t("home_hello", { name: user.display_name })}</h1>
      {user.role !== "user" && <p className="role">{t(`role_${user.role}`)}</p>}
      <p>{t("home_tagline")}</p>
      <div className="menu">
        <button className="button" onClick={() => push({ name: "properties" })}>
          {t("home_properties")}
        </button>
        {isStaff && (
          <button className="button secondary" onClick={() => push({ name: "moderation" })}>
            {modCount ? t("home_moderation_count", { count: String(modCount) }) : t("home_moderation")}
          </button>
        )}
        <button className="button secondary" onClick={() => push({ name: "profile" })}>
          {t("home_profile")}
        </button>
      </div>
      <p className="hint small">{t("home_soon")}</p>
    </Screen>
  );
}
