import { useCallback, useEffect, useMemo, useState } from "react";

import { api, setInitData, type Me, type Property, type User } from "./api/client";
import { BOT_USERNAME } from "./config";
import { t } from "./i18n";
import { Icon } from "./icons";
import { ModerationCase, ModerationQueue } from "./screens/Moderation";
import { ConsentAndName, PhoneStep } from "./screens/Onboarding";
import { Profile } from "./screens/Profile";
import { PropertyProfile } from "./screens/PropertyProfile";
import { NewProperty, OwnershipScreen, PropertyCard, PropertyList } from "./screens/Properties";
import { getWebApp, startParam } from "./telegram";
import { errorText, Mascot, MascotTip, NavContext, Screen, type Tab } from "./ui";

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
  // Стек екранів усередині вкладки: «Назад» повертає на попередній.
  const [stack, setStack] = useState<Route[]>([{ name: "home" }]);
  const [modCount, setModCount] = useState<number | null>(null);
  const [props, setProps] = useState<Property[] | null>(null);

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
        if (deep.length) setStack(deep);
      })
      .catch((e: unknown) => setState({ kind: "error", message: errorText(e), retry: true }));
  }, [webApp]);

  useEffect(load, [load]);

  const user = state.kind === "ready" ? state.me.user : null;
  const isStaff = user?.role === "moderator" || user?.role === "admin";
  const ready = !!user && user.status !== "deleted" && user.status !== "banned" && user.has_phone;
  const atHome = route.name === "home";

  useEffect(() => {
    if (!ready || !atHome) return;
    api.properties().then(setProps).catch(() => setProps(null));
    if (isStaff)
      api
        .moderationSummary()
        .then((s) => setModCount(s.ownership_pending))
        .catch(() => setModCount(null));
  }, [ready, isStaff, atHome]);

  const activeTab: Tab | null = (() => {
    const root = stack[0]?.name;
    if (root === "home" || root === "properties" || root === "moderation" || root === "profile") return root;
    return null;
  })();

  const nav = useMemo(
    () => ({
      active: activeTab,
      isStaff,
      modCount,
      go: (tab: Tab) => setStack([{ name: tab }]),
    }),
    [activeTab, isStaff, modCount],
  );

  if (!webApp) {
    return (
      <Screen center>
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
        <div className="skeleton" style={{ height: 160 }} />
        <div className="skeleton" />
        <div className="skeleton" />
      </Screen>
    );
  }
  if (state.kind === "error") {
    return (
      <Screen center>
        <Mascot lantern size="md" />
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
      <Screen center>
        <Mascot lantern size="md" />
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
      <Screen center>
        <h1>{t("banned_title")}</h1>
        <p>{t("banned_text")}</p>
      </Screen>
    );
  }
  if (!user.has_phone) {
    return <PhoneStep webApp={webApp} onDone={setUser} />;
  }

  let screen: JSX.Element;
  switch (route.name) {
    case "profile":
      screen = (
        <Profile
          user={user}
          webApp={webApp}
          onBack={stack.length > 1 ? back : undefined}
          onUserChange={setUser}
          onDeleted={() => setState({ kind: "deleted" })}
        />
      );
      break;
    case "properties":
      screen = (
        <PropertyList
          onBack={stack.length > 1 ? back : undefined}
          onOpen={(id) => push({ name: "property", id })}
          onNew={() => push({ name: "new-property" })}
        />
      );
      break;
    case "new-property":
      screen = <NewProperty onBack={back} onCreated={(p) => replace({ name: "property", id: p.id })} />;
      break;
    case "property":
      screen = (
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
      break;
    case "ownership":
      screen = <OwnershipScreen key={route.id} id={route.id} webApp={webApp} onBack={back} />;
      break;
    case "profile-form":
      screen = <PropertyProfile key={route.id} id={route.id} onBack={back} />;
      break;
    case "moderation":
      screen = (
        <ModerationQueue onBack={stack.length > 1 ? back : undefined} onOpen={(vid) => push({ name: "mod-case", vid })} />
      );
      break;
    case "mod-case":
      screen = <ModerationCase key={route.vid} vid={route.vid} onBack={back} />;
      break;
    case "home":
      screen = (
        <Home
          user={user}
          props={props}
          modCount={isStaff ? modCount : null}
          onTab={nav.go}
          onNewProperty={() => setStack([{ name: "properties" }, { name: "new-property" }])}
        />
      );
      break;
  }

  return <NavContext.Provider value={nav}>{screen}</NavContext.Provider>;
}

function Home({
  user,
  props,
  modCount,
  onTab,
  onNewProperty,
}: {
  user: User;
  props: Property[] | null;
  modCount: number | null;
  onTab: (tab: Tab) => void;
  onNewProperty: () => void;
}) {
  const count = props?.length ?? 0;
  const firstName = user.display_name.split(" ")[0];
  return (
    <Screen>
      <section className="hero">
        {user.role !== "user" && <span className="role">{t(`role_${user.role}`)}</span>}
        <h1>{t("home_hello", { name: firstName })}</h1>
        <p>{t("home_tagline")}</p>
        <img className="hero-mascot" src={`${import.meta.env.BASE_URL}../brand/mascot.svg`} alt="" />
      </section>

      {props && count === 0 && (
        <MascotTip>
          <p>{t("home_tip_first")}</p>
        </MascotTip>
      )}

      <div className="quick">
        <button className="quick-item" onClick={() => (count ? onTab("properties") : onNewProperty())}>
          <span className="row-icon">
            <Icon name={count ? "building" : "plus"} />
          </span>
          <span className="quick-title">{count ? t("home_q_properties") : t("home_q_add")}</span>
          <span className="quick-sub">{count ? t("home_q_properties_sub", { n: String(count) }) : t("home_q_add_sub")}</span>
        </button>
        <div className="quick-item soon" aria-disabled="true">
          <span className="row-icon">
            <Icon name="search" />
          </span>
          <span className="quick-title">{t("home_q_search")}</span>
          <span className="quick-sub">{t("home_q_soon")}</span>
        </div>
        {modCount !== null && (
          <button className="quick-item" onClick={() => onTab("moderation")}>
            <span className="row-icon">
              <Icon name="shield" />
            </span>
            <span className="quick-title">{t("home_moderation")}</span>
            <span className="quick-sub">{t("home_q_mod_sub", { n: String(modCount) })}</span>
          </button>
        )}
        <div className="quick-item soon" aria-disabled="true">
          <span className="row-icon">
            <Icon name="key" />
          </span>
          <span className="quick-title">{t("home_q_lease")}</span>
          <span className="quick-sub">{t("home_q_soon")}</span>
        </div>
      </div>

      <MascotTip tone="calm">
        <p>{t("home_soon")}</p>
      </MascotTip>
    </Screen>
  );
}
