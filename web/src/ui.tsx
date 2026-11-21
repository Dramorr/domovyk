import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { api, ApiFailure } from "./api/client";
import { t, type TextKey } from "./i18n";
import { Icon, type IconName } from "./icons";
import { getWebApp, nativeBackButton } from "./telegram";
import { Townscape } from "./townscape";

export const BRAND = `${import.meta.env.BASE_URL}../brand/`;

type Art = "mascot" | "mascot-lantern" | "logo-mark" | "app-icon";

/** Маскот і логотип у кольорах режиму: у власника Домовик синій, як і весь інтерфейс. */
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
  /** Непрочитані сповіщення — число на дзвіночку в шапці. */
  unread: number;
  openNotifications: () => void;
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
        {/* Ім'я й перемикач режиму в один рядок; хрестика немає — шторку ховає тап по тлу чи свайп. */}
        <div className="sheet-head menu-head">
          <h2>{nav.userName}</h2>
          <button
            className={`mode-chip ${nav.mode}`}
            onClick={pick(nav.switchMode)}
            aria-label={tenant ? t("mode_switch_to_landlord") : t("mode_switch_to_tenant")}
          >
            <Icon name={tenant ? "search" : "building"} size={16} />
            {tenant ? t("mode_tenant_short") : t("mode_landlord_short")}
            <Icon name="swap" size={14} />
          </button>
        </div>
        <nav className="menu-list" aria-label={t("nav_label")}>
          {main.map(row)}
        </nav>
        <div className="menu-list secondary">{more.map(row)}</div>
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

/** «Назад» — рідною кнопкою Telegram; true, якщо вона є (тоді своєї в шапці не треба). */
function useNativeBack(onBack?: () => void): boolean {
  const button = nativeBackButton(getWebApp());
  const latest = useRef(onBack);
  latest.current = onBack;
  const wanted = !!onBack;
  useEffect(() => {
    if (!button || !wanted) return;
    const handler = () => latest.current?.();
    button.onClick(handler);
    button.show();
    return () => {
      button.offClick(handler);
      button.hide();
    };
  }, [button, wanted]);
  return !!button;
}

export function Header({ onBack, title, actions }: { onBack?: () => void; title?: string; actions?: ReactNode }) {
  const nav = useContext(NavContext);
  const [menu, setMenu] = useState(false);
  const art = useArt();
  const native = useNativeBack(onBack);
  const logo = <img className="appbar-logo" src={art("app-icon")} alt="" width={34} height={34} />;
  return (
    <header className="appbar">
      {/* Запасна «Назад» — лише там, де Telegram не дає своєї (старий клієнт, браузер). */}
      {onBack && !native && (
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
      {/* Дії сторінки (дзвіночок сповіщень тощо) — праворуч, перед меню. */}
      {(actions || nav) && (
        <span className="appbar-actions">
          {actions}
          {/* Дзвіночок — на кожній сторінці: стрічка всіх сповіщень, звідти — на потрібну сторінку. */}
          {nav && (
            <button className="icon-button bell-button" onClick={nav.openNotifications} aria-label={t("ntf_feed_title")}>
              <Icon name="bell" size={24} />
              {nav.unread > 0 && <span className="dot">{nav.unread > 99 ? "99+" : nav.unread}</span>}
            </button>
          )}
        </span>
      )}
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

export type PageTab = {
  key: string;
  icon: IconName;
  label: string;
  /** Лічильник (непрочитані): червона крапка з числом. */
  count?: number;
  onClick: () => void;
  active?: boolean;
};

/** Вкладка сторінки, яку пам'ятаємо в межах сесії: повернулись із чату — та сама вкладка. */
export function useStoredTab<T extends string>(key: string, allowed: readonly T[], fallback: T): [T, (next: T) => void] {
  const [tab, setTab] = useState<T>(() => {
    try {
      const v = sessionStorage.getItem(key);
      return allowed.includes(v as T) ? (v as T) : fallback;
    } catch {
      return fallback;
    }
  });
  const go = useCallback(
    (next: T) => {
      try {
        sessionStorage.setItem(key, next);
      } catch {
        // Немає сховища — просто не пам'ятаємо.
      }
      setTab(next);
      window.scrollTo({ top: 0 });
    },
    [key],
  );
  return [tab, go];
}

/** Нижня панель розділів сторінки: лише локальні посилання (умови, люди, чат…).
 *  Глобальні розділи — у меню (ADR 0015). Немає розділів — немає й панелі. */
function PageTabs({ tabs }: { tabs: PageTab[] }) {
  return (
    <nav className="page-tabs" aria-label={t("page_tabs")}>
      {tabs.map((x) => (
        <button
          key={x.key}
          className={`page-tab${x.active ? " active" : ""}`}
          aria-current={x.active ? "page" : undefined}
          onClick={x.onClick}
        >
          <span className="page-tab-icon">
            <Icon name={x.icon} size={22} />
            {!!x.count && <span className="dot">{x.count > 99 ? "99+" : x.count}</span>}
          </span>
          {x.label}
        </button>
      ))}
    </nav>
  );
}

export function Screen({
  children,
  onBack,
  title,
  actions,
  tabs,
  center = false,
}: {
  children: ReactNode;
  onBack?: () => void;
  /** Заголовок у верхній панелі (на внутрішніх екранах). */
  title?: string;
  /** Іконки-дії сторінки в шапці. */
  actions?: ReactNode;
  /** Розділи сторінки в нижній панелі. */
  tabs?: PageTab[];
  /** Короткі екрани-повідомлення (завантаження, помилка, бан) — по центру. */
  center?: boolean;
}) {
  const withTabs = !!tabs && tabs.length > 1;
  return (
    <div className={`app${withTabs ? " with-tabs" : ""}`}>
      <Header onBack={onBack} title={title} actions={actions} />
      <main className={`screen${center ? " center" : ""}`}>{children}</main>
      {withTabs && <PageTabs tabs={tabs} />}
    </div>
  );
}

// ------------------------------------------------------------- аватар

// Файли аватарів у пам'яті сторінки: ключ — користувач + версія (нове фото — нова версія).
const avatarUrls = new Map<string, Promise<string>>();

function avatarUrl(userId: string, version: string): Promise<string> {
  const key = `${userId}:${version}`;
  let url = avatarUrls.get(key);
  if (!url) {
    url = api.avatarFile(userId, version).then(({ data }) => URL.createObjectURL(new Blob([data], { type: "image/jpeg" })));
    url.catch(() => avatarUrls.delete(key));
    avatarUrls.set(key, url);
  }
  return url;
}

/** Кружок-аватар: фото користувача або перша літера імені на кольорі режиму. */
export function Avatar({
  userId,
  version,
  name,
  size = 60,
}: {
  userId: string;
  version?: string | null;
  name: string;
  size?: number;
}) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setUrl(null);
    if (version) {
      avatarUrl(userId, version)
        .then((u) => alive && setUrl(u))
        .catch(() => undefined);
    }
    return () => {
      alive = false;
    };
  }, [userId, version]);
  return (
    <span className="avatar" style={{ width: size, height: size, fontSize: Math.round(size * 0.43) }} aria-hidden="true">
      {url ? <img src={url} alt="" draggable={false} /> : name.trim().charAt(0).toUpperCase()}
    </span>
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

/** Розгорнутий чи згорнутий блок — пам'ятаємо в межах сесії, щоб «Назад» не скидав вибір. */
function readFold(id: string): boolean | null {
  try {
    const v = sessionStorage.getItem(`fold:${id}`);
    return v === null ? null : v === "1";
  } catch {
    return null;
  }
}

function writeFold(id: string, open: boolean): void {
  try {
    sessionStorage.setItem(`fold:${id}`, open ? "1" : "0");
  } catch {
    // Немає сховища — просто не пам'ятаємо.
  }
}

/**
 * Блок-акордеон: заголовок, вміст — по кліку. Підсумку в заголовку немає: при
 * перемиканні він стрибав. Те, що треба бачити згорнутим, — у `aside` (лічильник).
 * Довідкове ховаємо, а те, що чекає дії людини, лишаємо звичайною карткою.
 */
/** Розділювач сторінки перед «небезпечними» діями. У темі «Містечко» — силует міста
 *  з однією червоною хаткою (townscape.tsx); у «plain» — тонка лінія (styles.css, оформлення). */
export function Divider() {
  return (
    <div className="deco-divider" aria-hidden="true">
      <Townscape />
    </div>
  );
}

export function Fold({
  id,
  title,
  aside,
  icon,
  defaultOpen = false,
  children,
}: {
  id: string;
  title: ReactNode;
  aside?: ReactNode;
  icon?: IconName;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(() => readFold(id) ?? defaultOpen);
  const toggle = () => {
    setOpen((o) => {
      writeFold(id, !o);
      return !o;
    });
  };
  return (
    <section className={`card fold${open ? " open" : ""}`}>
      <button type="button" className="fold-head" aria-expanded={open} onClick={toggle}>
        {icon && (
          <span className="fold-icon" aria-hidden>
            <Icon name={icon} size={20} />
          </span>
        )}
        <span className="fold-title">{title}</span>
        {aside && <span className="fold-aside">{aside}</span>}
        <span className="fold-chevron" aria-hidden>
          <Icon name="chevron" size={20} />
        </span>
      </button>
      <div className="fold-body" {...(open ? {} : { inert: "" })}>
        <div className="fold-inner">{children}</div>
      </div>
    </section>
  );
}

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
// Тип вулиці скорочуємо й ставимо попереду, як прийнято в адресах:
// «Болгарська вулиця» → «вул. Болгарська». Те саме робить бекенд (domain/properties.display_street).
const STREET_TYPES: [string, string][] = [
  ["вулиця", "вул."],
  ["вул", "вул."],
  ["проспект", "просп."],
  ["просп", "просп."],
  ["провулок", "пров."],
  ["пров", "пров."],
  ["бульвар", "бульв."],
  ["бульв", "бульв."],
  ["площа", "пл."],
  ["пл", "пл."],
  ["набережна", "наб."],
  ["узвіз", "узвіз"],
  ["майдан", "майдан"],
];

export function shortStreet(street: string): string {
  const value = street.replace(/\s+/g, " ").trim();
  for (const [word, short] of STREET_TYPES) {
    // Слово типу окремим словом (з крапкою чи без), будь-де в назві.
    const re = new RegExp(`(^|[\\s,])${word}\\.?(?=[\\s,]|$)`, "i");
    if (!re.test(value)) continue;
    const name = value.replace(re, "$1").replace(/\s+/g, " ").replace(/^[\s,.]+|[\s,.]+$/g, "");
    return name ? `${short} ${name}` : value;
  }
  return value;
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

export function errorReason(e: unknown): string | undefined {
  return e instanceof ApiFailure ? e.reason : undefined;
}

/** Заголовок шторки з хрестиком. */
export function SheetHead({ title, onClose }: { title: string; onClose: () => void }) {
  return (
    <div className="sheet-head">
      <h2>{title}</h2>
      <button className="icon-button" aria-label={t("ab_close")} onClick={onClose}>
        <Icon name="close" size={20} />
      </button>
    </div>
  );
}

export function errorField(e: unknown): string | undefined {
  return e instanceof ApiFailure ? e.field : undefined;
}
