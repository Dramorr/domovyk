import { useCallback, useEffect, useMemo, useState } from "react";

import { api, setInitData, type Invite, type Lease, type Me, type Property, type User } from "./api/client";
import { BOT_USERNAME } from "./config";
import { t } from "./i18n";
import { Chat } from "./screens/Chat";
import { InviteAccept, InviteShare, LeaseCard, LeaseForm, LeaseNotifications, TenantHome } from "./screens/Leases";
import { ModerationCase, ModerationQueue, SignatureReview } from "./screens/Moderation";
import { ConsentAndName, ModeChoice, PhoneStep } from "./screens/Onboarding";
import { Profile } from "./screens/Profile";
import { PropertyProfile } from "./screens/PropertyProfile";
import { NewProperty, OwnershipScreen, PropertyCard, PropertyList } from "./screens/Properties";
import { getWebApp, startParam } from "./telegram";
import { errorText, Mascot, MascotTip, type Mode, NavContext, Screen, type Tab } from "./ui";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string; retry: boolean }
  | { kind: "ready"; me: Me }
  | { kind: "deleted" };

type Route =
  | { name: "home" }
  | { name: "search" }
  | { name: "profile" }
  | { name: "new-property" }
  | { name: "property"; id: string }
  | { name: "ownership"; id: string }
  | { name: "profile-form"; id: string }
  | { name: "moderation" }
  | { name: "mod-case"; vid: string }
  | { name: "mod-sig"; sid: string }
  | { name: "lease"; id: string }
  | { name: "lease-new"; propertyId: string }
  | { name: "lease-edit"; id: string }
  | { name: "lease-propose"; id: string }
  | { name: "invite-share"; leaseId: string; invite: Invite }
  | { name: "invite"; token: string }
  | { name: "chat"; id: string }
  | { name: "lease-notify"; id: string };

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Екран із deep link (кнопки в сповіщеннях бота): prop_, lease_, chat_, inv_, mod. */
function routeFromStart(param: string | null, isStaff: boolean): Route[] {
  if (!param) return [];
  if (param === "mod" && isStaff) return [{ name: "moderation" }];
  if (param.startsWith("inv_")) return [{ name: "home" }, { name: "invite", token: param.slice(4) }];
  const leaseId = param.startsWith("lease_") ? param.slice(6) : param.startsWith("chat_") ? param.slice(5) : "";
  if (UUID_RE.test(leaseId)) {
    const base: Route[] = [{ name: "home" }, { name: "lease", id: leaseId }];
    return param.startsWith("chat_") ? [...base, { name: "chat", id: leaseId }] : base;
  }
  const id = param.startsWith("prop_") ? param.slice(5) : "";
  if (UUID_RE.test(id)) return [{ name: "home" }, { name: "property", id }];
  return [];
}

function modeForRoute(routes: Route[]): Mode | null {
  const root = routes[0]?.name;
  if (routes.some((r) => r.name === "invite") || root === "search") return "tenant";
  if (routes.some((r) => r.name === "property") || root === "moderation") return "landlord";
  return null;
}

export function App() {
  const webApp = getWebApp();
  const [state, setState] = useState<State>({ kind: "loading" });
  // Стек екранів усередині вкладки: «Назад» повертає на попередній.
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
      .then(async (me) => {
        const staff = me.user?.role === "moderator" || me.user?.role === "admin";
        const deep = routeFromStart(startParam(webApp), staff);
        // Посилання зі сповіщення саме перемикає режим (ADR 0006).
        const needed = modeForRoute(deep);
        if (needed && me.user?.mode && me.user.mode !== needed) {
          me = { ...me, user: await api.setMode(needed).catch(() => me.user) };
        }
        setState({ kind: "ready", me });
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
    if (isStaff)
      api
        .moderationSummary()
        .then((s) => setModCount(s.ownership_pending + (s.signatures_pending ?? 0)))
        .catch(() => setModCount(null));
  }, [ready, isStaff, atHome]);

  const activeTab: Tab | null = (() => {
    const root = stack[0]?.name;
    if (root === "home" || root === "search" || root === "moderation" || root === "profile") return root;
    return null;
  })();

  const mode: Mode = user?.mode ?? "landlord";
  const switchMode = useCallback(() => {
    const next: Mode = mode === "tenant" ? "landlord" : "tenant";
    api
      .setMode(next)
      .then((u) => {
        setState((s) => (s.kind === "ready" ? { kind: "ready", me: { ...s.me, user: u } } : s));
        setStack([{ name: "home" }]);
      })
      .catch(() => undefined);
  }, [mode]);

  const nav = useMemo(
    () => ({
      active: activeTab,
      mode,
      isStaff,
      modCount,
      go: (tab: Tab) => setStack([{ name: tab }]),
      switchMode,
    }),
    [activeTab, mode, isStaff, modCount, switchMode],
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
  if (!user.mode) {
    return <ModeChoice onDone={setUser} />;
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
          onSwitchMode={switchMode}
        />
      );
      break;
    case "search":
      screen = (
        <Screen>
          <h1>{t("search_title")}</h1>
          <Mascot size="md" />
          <MascotTip>
            <p>{t("search_soon")}</p>
          </MascotTip>
        </Screen>
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
          onNewLease={(prop) => push({ name: "lease-new", propertyId: prop.id })}
          onOpenLease={(id) => push({ name: "lease", id })}
          onChat={(id) => push({ name: "chat", id })}
          onDeleted={back}
        />
      );
      break;
    case "ownership":
      screen = <OwnershipScreen key={route.id} id={route.id} webApp={webApp} onBack={back} />;
      break;
    case "profile-form":
      screen = <PropertyProfile key={route.id} id={route.id} webApp={webApp} onBack={back} />;
      break;
    case "moderation":
      screen = (
        <ModerationQueue
          onBack={stack.length > 1 ? back : undefined}
          onOpen={(vid) => push({ name: "mod-case", vid })}
          onOpenSignature={(sid) => push({ name: "mod-sig", sid })}
        />
      );
      break;
    case "mod-sig":
      screen = <SignatureReview key={route.sid} sid={route.sid} onBack={back} />;
      break;
    case "mod-case":
      screen = <ModerationCase key={route.vid} vid={route.vid} onBack={back} />;
      break;
    case "lease":
      screen = (
        <LeaseCard
          key={route.id}
          id={route.id}
          webApp={webApp}
          onBack={back}
          onChat={() => push({ name: "chat", id: route.id })}
          onEdit={() => push({ name: "lease-edit", id: route.id })}
          onPropose={() => push({ name: "lease-propose", id: route.id })}
          onNotify={() => push({ name: "lease-notify", id: route.id })}
          onInvite={(invite) => push({ name: "invite-share", leaseId: route.id, invite })}
        />
      );
      break;
    case "lease-new":
      screen = (
        <LeaseFormLoader
          propertyId={route.propertyId}
          onBack={back}
          onOpenLease={(id) =>
            setStack([{ name: "home" }, { name: "property", id: route.propertyId }, { name: "lease", id }])
          }
          onCreated={(lease, invite) =>
            setStack([
              { name: "home" },
              { name: "property", id: lease.property_id },
              { name: "lease", id: lease.id },
              { name: "invite-share", leaseId: lease.id, invite },
            ])
          }
        />
      );
      break;
    case "lease-edit":
      screen = <LeaseFormLoader leaseId={route.id} onBack={back} onSaved={back} />;
      break;
    case "lease-propose":
      screen = <LeaseFormLoader leaseId={route.id} propose onBack={back} onSaved={back} />;
      break;
    case "invite-share":
      screen = <InviteShare invite={route.invite} webApp={webApp} onDone={back} />;
      break;
    case "invite":
      screen = (
        <InviteAccept
          token={route.token}
          onBack={back}
          onJoined={(lease) => setStack([{ name: "home" }, { name: "lease", id: lease.id }])}
        />
      );
      break;
    case "chat":
      screen = <Chat key={route.id} leaseId={route.id} onBack={back} />;
      break;
    case "lease-notify":
      screen = <LeaseNotifications id={route.id} onBack={back} />;
      break;
    case "home":
      // Головна = робочий екран режиму: власність (власник) або моя оренда (орендар).
      screen =
        mode === "landlord" ? (
          <PropertyList
            hero={<Hero user={user} />}
            onOpen={(id) => push({ name: "property", id })}
            onNew={() => push({ name: "new-property" })}
          />
        ) : (
          <TenantHome
            hero={<Hero user={user} />}
            webApp={webApp}
            onOpen={(id) => push({ name: "lease", id })}
            onSearch={() => nav.go("search")}
          />
        );
      break;
  }

  return <NavContext.Provider value={nav}>{screen}</NavContext.Provider>;
}

function Hero({ user }: { user: User }) {
  const firstName = user.display_name.split(" ")[0];
  return (
    <section className="hero">
      {user.role !== "user" && <span className="role">{t(`role_${user.role}`)}</span>}
      <h1>{t("home_hello", { name: firstName })}</h1>
      <p>{t("home_tagline")}</p>
      <img className="hero-mascot" src={`${import.meta.env.BASE_URL}../brand/mascot.svg`} alt="" />
    </section>
  );
}

/** Форма оренди потребує об'єкт (нова) або саму оренду (зміна умов) — довантажуємо їх. */
function LeaseFormLoader({
  propertyId,
  leaseId,
  onBack,
  onCreated,
  onSaved,
  onOpenLease,
  propose = false,
}: {
  propertyId?: string;
  leaseId?: string;
  onBack: () => void;
  onCreated?: (lease: Lease, invite: Invite) => void;
  onSaved?: (lease: Lease) => void;
  onOpenLease?: (id: string) => void;
  propose?: boolean;
}) {
  const [property, setProperty] = useState<Property | null>(null);
  const [lease, setLease] = useState<Lease | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (propertyId) api.property(propertyId).then(setProperty).catch((e: unknown) => setError(errorText(e)));
    if (leaseId) api.lease(leaseId).then(setLease).catch((e: unknown) => setError(errorText(e)));
  }, [propertyId, leaseId]);
  if ((propertyId && !property) || (leaseId && !lease)) {
    return <Screen onBack={onBack}>{error ? <p className="error">{error}</p> : <div className="skeleton" />}</Screen>;
  }
  return (
    <LeaseForm
      property={property ?? undefined}
      lease={lease ?? undefined}
      propose={propose}
      onBack={onBack}
      onCreated={onCreated}
      onSaved={onSaved}
      onOpenLease={onOpenLease}
    />
  );
}
