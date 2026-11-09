import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { ApiFailure } from "./api/client";
import { t, type TextKey } from "./i18n";
import { Icon, type IconName } from "./icons";

export const BRAND = `${import.meta.env.BASE_URL}../brand/`;

type Art = "mascot" | "mascot-lantern" | "logo-mark" | "app-icon";

/** Маскот і логотип у кольорах режиму: у власника Домовик жовтий, як і весь інтерфейс. */
export function useArt(): (name: Art) => string {
  const nav = useContext(NavContext);
  const landlord = nav?.mode === "landlord";
  return (name) => `${BRAND}${name}${landlord ? "-landlord" : ""}.svg`;
}

// ------------------------------------------------------------- навігація

export type Tab = "home" | "search" | "moderation" | "profile";
export type Mode = "tenant" | "landlord";

type Nav = {
  active: Tab | null;
  mode: Mode;
  isStaff: boolean;
  modCount: number | null;
  userName: string;
  go: (tab: Tab) => void;
  switchMode: () => void;
  /** Зовнішнє посилання (правила, сайт) і посилання в Telegram (бот). */
  openLink: (url: string) => void;
  openTelegram: (url: string) => void;
  links: { terms: string; privacy: string; about: string; bot: string };
};

/** Меню й шапка зареєстрованого користувача (App дає контекст). */
export const NavContext = createContext<Nav | null>(null);

type MenuItem = { key: string; icon: IconName; label: string; count?: number | null; onClick: () => void; active?: boolean };

/** Меню знизу (ADR 0015): режим, розділи, корисні посилання. Нижньої панелі немає. */
function MenuSheet({ nav, onClose }: { nav: Nav; onClose: () => void }) {
  const tenant = nav.mode === "tenant";
  // Спершу шторка плавно ховається, потім перехід — новий екран не обриває анімацію.
  const closeRef = useRef<() => void>(onClose);
  const pick = (fn: () => void) => () => {
    closeRef.current();
    window.setTimeout(fn, SHEET_OUT_MS);
  };
  const main: MenuItem[] = [
    { key: "home", icon: "home", label: t("nav_home"), onClick: pick(() => nav.go("home")), active: nav.active === "home" },
    ...(tenant
      ? [{ key: "search", icon: "search" as const, label: t("nav_search"), onClick: pick(() => nav.go("search")), active: nav.active === "search" }]
      : []),
    ...(nav.isStaff
      ? [
          {
            key: "moderation",
            icon: "shield" as const,
            label: t("nav_moderation"),
            count: nav.modCount,
            onClick: pick(() => nav.go("moderation")),
            active: nav.active === "moderation",
          },
        ]
      : []),
    { key: "profile", icon: "user", label: t("menu_profile"), onClick: pick(() => nav.go("profile")), active: nav.active === "profile" },
  ];
  const more: MenuItem[] = [
    ...(nav.links.bot ? [{ key: "bot", icon: "send" as const, label: t("menu_write_us"), onClick: pick(() => nav.openTelegram(nav.links.bot)) }] : []),
    { key: "about", icon: "info", label: t("menu_about"), onClick: pick(() => nav.openLink(nav.links.about)) },
    ...(nav.links.terms ? [{ key: "terms", icon: "doc" as const, label: t("onb_terms"), onClick: pick(() => nav.openLink(nav.links.terms)) }] : []),
    ...(nav.links.privacy
      ? [{ key: "privacy", icon: "shield" as const, label: t("onb_privacy"), onClick: pick(() => nav.openLink(nav.links.privacy)) }]
      : []),
  ];
  const row = (x: MenuItem) => (
    <button key={x.key} className={`menu-row${x.active ? " active" : ""}`} onClick={x.onClick}>
      <Icon name={x.icon} size={20} />
      <span className="menu-label">{x.label}</span>
      {!!x.count && <span className="count">{x.count}</span>}
    </button>
  );
  return (
    <Sheet label={t("menu_title")} onClose={onClose} className="menu-sheet">
      {(close) => {
        closeRef.current = close;
        return (
          <>
        <div className="sheet-head">
          <h2>{nav.userName}</h2>
          <button className="icon-button" aria-label={t("ph_close")} onClick={close}>
            <Icon name="close" size={20} />
          </button>
        </div>
        <button className="mode-card" onClick={pick(nav.switchMode)}>
          <span className="row-icon">
            <Icon name={tenant ? "search" : "building"} />
          </span>
          <span className="row-body">
            <span className="row-sub">{tenant ? t("menu_mode_tenant") : t("menu_mode_landlord")}</span>
            <span className="row-title">{tenant ? t("mode_card_to_landlord") : t("mode_card_to_tenant")}</span>
          </span>
          <Icon name="swap" size={20} />
        </button>
        <nav className="menu-list" aria-label={t("nav_label")}>
          {main.map(row)}
        </nav>
        <div className="menu-list secondary">{more.map(row)}</div>
        <p className="hint small center-text">{t("menu_beta")}</p>
          </>
        );
      }}
    </Sheet>
  );
}

// ------------------------------------------------------------- шторка

const SHEET_OUT_MS = 220;
const SWIPE_CLOSE_PX = 90;

/**
 * Нижня шторка: виїжджає й ховається плавно, закривається тапом по тлу,
 * хрестиком (`close` у children) або свайпом униз за верхню частину.
 */
export function Sheet({
  label,
  onClose,
  className,
  children,
}: {
  label: string;
  onClose: () => void;
  className?: string;
  children: (close: () => void) => ReactNode;
}) {
  const [closing, setClosing] = useState(false);
  const [drag, setDrag] = useState(0);
  const start = useRef<number | null>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const close = useCallback(() => {
    setClosing(true);
    timer.current = window.setTimeout(onClose, SHEET_OUT_MS);
  }, [onClose]);

  // Свайп униз — лише з верхньої частини (ручка й заголовок), щоб не заважати прокрутці вмісту.
  function onTouchStart(e: React.TouchEvent) {
    const target = e.target as HTMLElement;
    if (!target.closest(".sheet-handle, .sheet-head") || target.closest("button")) return;
    start.current = e.touches[0]?.clientY ?? null;
  }
  function onTouchMove(e: React.TouchEvent) {
    if (start.current === null) return;
    setDrag(Math.max(0, (e.touches[0]?.clientY ?? start.current) - start.current));
  }
  function onTouchEnd() {
    if (start.current === null) return;
    start.current = null;
    if (drag > SWIPE_CLOSE_PX) close();
    else setDrag(0);
  }

  return createPortal(
    <div className={`sheet-backdrop${closing ? " closing" : ""}`} onClick={close}>
      <div
        className={`sheet${className ? ` ${className}` : ""}${drag ? " dragging" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        style={drag ? { transform: `translateY(${drag}px)` } : undefined}
        onClick={(e) => e.stopPropagation()}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
      >
        <div className="sheet-handle" />
        {children(close)}
      </div>
    </div>,
    document.body,
  );
}


// ------------------------------------------------------------- оболонка

export function Header({ onBack, title }: { onBack?: () => void; title?: string }) {
  const nav = useContext(NavContext);
  const [menu, setMenu] = useState(false);
  const art = useArt();
  const logo = <img className="appbar-logo" src={art("app-icon")} alt="" width={34} height={34} />;
  return (
    <header className="appbar">
      {onBack && (
        <button className="icon-button" onClick={onBack} aria-label={t("back")}>
          <Icon name="back" />
        </button>
      )}
      {/* Логотип — на головну (як на сайтах). */}
      {nav ? (
        <button className="logo-button" onClick={() => nav.go("home")} aria-label={t("nav_home")}>
          {logo}
        </button>
      ) : (
        !onBack && logo
      )}
      {title ? <span className="appbar-title">{title}</span> : <span className="brand">Домовик</span>}
      {nav ? (
        <button className="icon-button menu-button" onClick={() => setMenu(true)} aria-label={t("menu_title")} aria-haspopup="dialog">
          <Icon name="menu" size={28} />
          {!!nav.modCount && nav.isStaff && <span className="dot">{nav.modCount}</span>}
        </button>
      ) : (
        <span className="badge">{t("beta")}</span>
      )}
      {nav && menu && <MenuSheet nav={nav} onClose={() => setMenu(false)} />}
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
  return (
    <div className="app">
      <Header onBack={onBack} title={title} />
      <main className={`screen${center ? " center" : ""}`}>{children}</main>
    </div>
  );
}

// ------------------------------------------------------------- маскот

export function Mascot({ lantern = false, size = "lg" }: { lantern?: boolean; size?: "lg" | "md" }) {
  const art = useArt();
  return <img className={`mascot ${size}`} src={art(lantern ? "mascot-lantern" : "mascot")} alt="" />;
}

/** Домовик підказує: голова маскота + бульбашка. Голос — тепло й коротко (CLAUDE.md). */
export function MascotTip({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "calm" }) {
  const art = useArt();
  return (
    <div className={`tip ${tone}`}>
      <img className="tip-avatar" src={art("logo-mark")} alt="" width={40} height={40} />
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
