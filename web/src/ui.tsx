import { createContext, useContext, type ReactNode } from "react";

import { ApiFailure } from "./api/client";
import { t, type TextKey } from "./i18n";
import { Icon, type IconName } from "./icons";

export const BRAND = `${import.meta.env.BASE_URL}../brand/`;

// ------------------------------------------------------------- навігація

export type Tab = "home" | "search" | "properties" | "leases" | "moderation" | "profile";
export type Mode = "tenant" | "landlord";

type Nav = {
  active: Tab | null;
  mode: Mode;
  isStaff: boolean;
  modCount: number | null;
  go: (tab: Tab) => void;
  switchMode: () => void;
};

/** Нижня навігація є лише в зареєстрованого користувача (App дає контекст). */
export const NavContext = createContext<Nav | null>(null);

// Своя навігація для кожного режиму (ADR 0006); «Модерація» — лише модераторам.
const TABS: Record<Mode, { tab: Tab; icon: IconName; label: TextKey; staff?: boolean }[]> = {
  tenant: [
    { tab: "home", icon: "home", label: "nav_home" },
    { tab: "search", icon: "search", label: "nav_search" },
    { tab: "leases", icon: "key", label: "nav_my_lease" },
    { tab: "moderation", icon: "shield", label: "nav_moderation", staff: true },
    { tab: "profile", icon: "user", label: "nav_profile" },
  ],
  landlord: [
    { tab: "home", icon: "home", label: "nav_home" },
    { tab: "properties", icon: "building", label: "nav_properties" },
    { tab: "leases", icon: "key", label: "nav_leases" },
    { tab: "moderation", icon: "shield", label: "nav_moderation", staff: true },
    { tab: "profile", icon: "user", label: "nav_profile" },
  ],
};

function BottomNav({ nav }: { nav: Nav }) {
  return (
    <nav className="tabbar" aria-label={t("nav_label")}>
      {TABS[nav.mode].filter((x) => !x.staff || nav.isStaff).map((x) => (
        <button
          key={x.tab}
          className={`tab${nav.active === x.tab ? " active" : ""}`}
          aria-current={nav.active === x.tab ? "page" : undefined}
          onClick={() => nav.go(x.tab)}
        >
          <span className="tab-icon">
            <Icon name={x.icon} />
            {x.tab === "moderation" && !!nav.modCount && <span className="dot">{nav.modCount}</span>}
          </span>
          <span className="tab-label">{t(x.label)}</span>
        </button>
      ))}
    </nav>
  );
}

// ------------------------------------------------------------- оболонка

/** Чип режиму в шапці: показує, де ти зараз, і перемикає (ADR 0006). */
function ModeChip({ nav }: { nav: Nav }) {
  const tenant = nav.mode === "tenant";
  return (
    <button
      className={`mode-chip ${nav.mode}`}
      onClick={nav.switchMode}
      aria-label={tenant ? t("mode_switch_to_landlord") : t("mode_switch_to_tenant")}
    >
      <Icon name={tenant ? "search" : "building"} size={16} />
      {tenant ? t("mode_tenant_short") : t("mode_landlord_short")}
      <Icon name="swap" size={14} />
    </button>
  );
}

export function Header({ onBack, title }: { onBack?: () => void; title?: string }) {
  const nav = useContext(NavContext);
  return (
    <header className="appbar">
      {onBack ? (
        <button className="icon-button" onClick={onBack} aria-label={t("back")}>
          <Icon name="back" />
        </button>
      ) : (
        <img className="appbar-logo" src={`${BRAND}app-icon.svg`} alt="" width={34} height={34} />
      )}
      {title ? <span className="appbar-title">{title}</span> : <span className="brand">Домовик</span>}
      {nav ? <ModeChip nav={nav} /> : <span className="badge">{t("beta")}</span>}
    </header>
  );
}

export function Screen({
  children,
  onBack,
  title,
  center = false,
}: {
  children: ReactNode;
  onBack?: () => void;
  /** Заголовок у верхній панелі (на внутрішніх екранах). */
  title?: string;
  /** Короткі екрани-повідомлення (завантаження, помилка, бан) — по центру. */
  center?: boolean;
}) {
  const nav = useContext(NavContext);
  return (
    <div className={`app${nav ? " with-tabs" : ""}`}>
      <Header onBack={onBack} title={title} />
      <main className={`screen${center ? " center" : ""}`}>{children}</main>
      {nav && <BottomNav nav={nav} />}
    </div>
  );
}

// ------------------------------------------------------------- маскот

export function Mascot({ lantern = false, size = "lg" }: { lantern?: boolean; size?: "lg" | "md" }) {
  return <img className={`mascot ${size}`} src={`${BRAND}${lantern ? "mascot-lantern" : "mascot"}.svg`} alt="" />;
}

/** Домовик підказує: голова маскота + бульбашка. Голос — тепло й коротко (CLAUDE.md). */
export function MascotTip({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "calm" }) {
  return (
    <div className={`tip ${tone}`}>
      <img className="tip-avatar" src={`${BRAND}logo-mark.svg`} alt="" width={40} height={40} />
      <div className="tip-bubble">{children}</div>
    </div>
  );
}

// ------------------------------------------------------------- дрібні компоненти

export function Chip({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "ok" | "warn" | "info" | "bad" }) {
  return <span className={`chip-status ${tone}`}>{children}</span>;
}

export function Ring({ percent, size = 56 }: { percent: number; size?: number }) {
  const r = 22;
  const c = 2 * Math.PI * r;
  const level = percent >= 75 ? "high" : percent >= 40 ? "medium" : "low";
  return (
    <svg className={`ring ${level}`} width={size} height={size} viewBox="0 0 52 52" role="img" aria-label={`${percent}%`}>
      <circle className="ring-track" cx="26" cy="26" r={r} />
      <circle
        className="ring-fill"
        cx="26"
        cy="26"
        r={r}
        strokeDasharray={c}
        strokeDashoffset={c * (1 - Math.min(100, Math.max(0, percent)) / 100)}
      />
      <text x="26" y="30" textAnchor="middle">
        {percent}%
      </text>
    </svg>
  );
}

export function ListRow({
  title,
  sub,
  icon,
  right,
  onClick,
}: {
  title: ReactNode;
  sub?: ReactNode;
  icon?: IconName;
  right?: ReactNode;
  onClick?: () => void;
}) {
  return (
    <button className="row" onClick={onClick}>
      {icon && (
        <span className="row-icon">
          <Icon name={icon} />
        </span>
      )}
      <span className="row-body">
        <span className="row-title">{title}</span>
        {sub && <span className="row-sub">{sub}</span>}
      </span>
      {right}
      <span className="row-chevron">
        <Icon name="chevron" size={18} />
      </span>
    </button>
  );
}

// ------------------------------------------------------------- тексти й формати

/** Текст для помилки API: спершу за доменним кодом, інакше — за типом. */
export function errorText(e: unknown): string {
  if (e instanceof ApiFailure) {
    if (e.kind === "domain") return t("error_domain");
    return t(`error_${e.kind}` as TextKey);
  }
  return t("error_network");
}

/** Адреса об'єкта для показу власнику (повна: вона його). */
// Тип вулиці скорочуємо, як прийнято в адресах: «вулиця Шевченка» → «вул. Шевченка».
const STREET_TYPES: [RegExp, string][] = [
  [/(^|\s)вулиця(?=\s|$)/i, "$1вул."],
  [/(^|\s)проспект(?=\s|$)/i, "$1просп."],
  [/(^|\s)провулок(?=\s|$)/i, "$1пров."],
  [/(^|\s)бульвар(?=\s|$)/i, "$1бульв."],
  [/(^|\s)площа(?=\s|$)/i, "$1пл."],
];

export function shortStreet(street: string): string {
  return STREET_TYPES.reduce((s, [re, short]) => s.replace(re, short), street);
}

export function formatAddress(p: { street: string; house_number: string; apartment?: string | null }): string {
  const street = shortStreet(p.street);
  return p.apartment
    ? t("address_apt", { street, house: p.house_number, apartment: p.apartment })
    : t("address_house", { street, house: p.house_number });
}

export function roomsText(rooms: number): string {
  return rooms === 0 ? t("rooms_studio") : t("rooms_n", { n: String(rooms) });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("uk-UA", { timeZone: "Europe/Kyiv" });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("uk-UA", {
    timeZone: "Europe/Kyiv",
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Код доменної помилки, якщо це вона. */
export function errorCode(e: unknown): string | undefined {
  return e instanceof ApiFailure && e.kind === "domain" ? e.code : undefined;
}

export function errorField(e: unknown): string | undefined {
  return e instanceof ApiFailure ? e.field : undefined;
}
