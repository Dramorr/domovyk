// Пошук для орендаря (M6, BOT_FLOWS §5, FR-SRC-1..6). Каталогу немає: спершу фільтр, потім
// результати (FR-SRC-1). Адреса — лише вулиця й район; будинок, квартира й поверх відкриються
// після схвалення заявки (FR-LST-4). Останній фільтр пам'ятаємо на пристрої.

import { useEffect, useState } from "react";

import { api, ApiFailure, type Amenity, type Application, type ListingCard, type ListingSummary, type SavedSearch, type SearchIn, type SearchOptions, type SearchPage } from "../api/client";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import { PROFILE_SECTIONS, type FieldSpec } from "../profileSpec";
import { type WebApp } from "../telegram";
import { Chip, errorText, Fold, formatDate, MascotTip, roomsText, Screen, shortStreet } from "../ui";
import { ApplicationChip, ApplySheet } from "./Applications";
import { PhotoImage, PhotoSlider } from "./Photos";

const OPEN_APP: Application["status"][] = ["pending", "approved", "showing_scheduled"];

const k = (key: string, vars?: Record<string, string>) => t(key as TextKey, vars);
const STORE = "domovyk.search.v1";
const ROOMS = [0, 1, 2, 3, 4];

type Filter = Omit<SearchIn, "offset">;

const EMPTY: Filter = {
  district_ids: [],
  rooms: [],
  price_min: null,
  price_max: null,
  area_min: null,
  floor_not_first: false,
  floor_not_last: false,
  pets: false,
  children: false,
  registration: false,
  amenities: [],
  completeness_min: 0,
};

function loadFilter(): Filter {
  try {
    const raw = localStorage.getItem(STORE);
    return raw ? { ...EMPTY, ...(JSON.parse(raw) as Filter) } : EMPTY;
  } catch {
    return EMPTY;
  }
}

function saveFilter(f: Filter): void {
  try {
    localStorage.setItem(STORE, JSON.stringify(f));
  } catch {
    // Немає сховища (приватний режим) — просто не пам'ятаємо фільтр.
  }
}

function money(n: number): string {
  return n.toLocaleString("uk-UA");
}

function numOrNull(raw: string): number | null {
  const clean = raw.replace(/\s/g, "");
  return clean === "" || Number.isNaN(Number(clean)) ? null : Number(clean);
}

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((x) => x !== value) : [...list, value];
}

function roomsLabel(r: number): string {
  if (r === 0) return t("rooms_studio");
  return r === 4 ? "4+" : String(r);
}

function placeText(x: Pick<ListingSummary, "district" | "street">): string {
  return [x.district, shortStreet(x.street)].filter(Boolean).join(" · ");
}

/** Назва збереженого пошуку з фільтра: «1–2 кімн., до 20 000 ₴, Галицький». Людина може змінити. */
function filterSummary(f: Filter, options: SearchOptions | null): string {
  const parts: string[] = [];
  const rooms = [...(f.rooms ?? [])].sort();
  if (rooms.length) parts.push(k("src_sum_rooms", { rooms: rooms.map(roomsLabel).join("/") }));
  if (f.price_min) parts.push(k("src_sum_from", { n: money(f.price_min) }));
  if (f.price_max) parts.push(k("src_sum_to", { n: money(f.price_max) }));
  const names = (options?.districts ?? []).filter((d) => f.district_ids?.includes(d.id)).map((d) => d.name.replace(/ район$/, ""));
  if (names.length) parts.push(names.length > 2 ? `${names.slice(0, 2).join(", ")}…` : names.join(", "));
  const text = parts.join(", ") || t("src_sum_any");
  return text.length > 60 ? `${text.slice(0, 59)}…` : text;
}

// ------------------------------------------------------------- SRC: фільтр і результати

// Екран розмонтовується, коли відкриваємо картку; повертаючись, людина чекає ті самі результати.
type View = "form" | "results" | "saved";
type Session = { page: SearchPage; items: ListingSummary[]; view: View };
let lastSession: Session | null = null;

export function SearchScreen({
  onOpen,
  onFavorites,
  onApplications,
  initialView,
}: {
  onOpen: (id: string) => void;
  onFavorites: () => void;
  onApplications: () => void;
  initialView?: View;
}) {
  const [options, setOptions] = useState<SearchOptions | null>(null);
  const [filter, setFilter] = useState<Filter>(loadFilter);
  const [page, setPage] = useState<SearchPage | null>(lastSession?.page ?? null);
  const [items, setItems] = useState<ListingSummary[]>(lastSession?.items ?? []);
  const [view, setView] = useState<View>(initialView ?? lastSession?.view ?? "form");
  const [saved, setSaved] = useState<SavedSearch[] | null>(null);
  const [savedNow, setSavedNow] = useState(false);
  const [saveName, setSaveName] = useState("");

  useEffect(() => {
    lastSession = page ? { page, items, view } : null;
  }, [page, items, view]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .savedSearches()
      .then(setSaved)
      .catch(() => setSaved([]));
  }, []);

  useEffect(() => {
    api
      .searchOptions(filter.city_code ?? undefined)
      .then(setOptions)
      .catch((e: unknown) => setError(errorText(e)));
  }, [filter.city_code]);

  async function run(offset: number, f: Filter = filter) {
    setBusy(true);
    setError(null);
    try {
      const result = await api.search({ ...f, offset });
      setPage(result);
      setItems((prev) => (offset === 0 ? result.items : [...prev, ...result.items]));
      setView("results");
      if (offset === 0) {
        saveFilter(f);
        setSavedNow(false);
        setSaveName(filterSummary(f, options));
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const patch = (p: Partial<Filter>) => setFilter((f) => ({ ...f, ...p }));

  async function saveSearch() {
    setBusy(true);
    setError(null);
    try {
      const item = await api.saveSearch(saveName.trim() || filterSummary(filter, options), { ...filter, offset: 0 });
      setSaved((xs) => [...(xs ?? []), item]);
      setSavedNow(true);
    } catch (e) {
      setError(e instanceof ApiFailure && e.reason === "limit" ? t("src_saved_limit") : errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function savedAction(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (view === "saved") {
    return (
      <Screen onBack={() => setView("form")}>
        <h1 className="left">{t("src_saved_title")}</h1>
        <p className="hint left">{t("src_saved_intro")}</p>
        {saved && saved.length === 0 && <MascotTip tone="calm">{t("src_saved_empty")}</MascotTip>}
        {(saved ?? []).map((x) => (
          <section key={x.id} className="card">
            <p className="row-title">{x.name}</p>
            <div className="buttons">
              <button
                className="button"
                disabled={busy}
                onClick={() => {
                  const next = { ...EMPTY, ...x.criteria };
                  setFilter(next);
                  void run(0, next);
                }}
              >
                <Icon name="search" size={20} />
                {t("src_saved_show")}
              </button>
              <button
                className="button secondary"
                disabled={busy}
                onClick={() =>
                  savedAction(async () => {
                    const item = await api.setSearchNotify(x.id, !x.notify);
                    setSaved((xs) => xs?.map((y) => (y.id === x.id ? item : y)) ?? null);
                  })
                }
              >
                {x.notify ? t("src_saved_notify_on") : t("src_saved_notify_off")}
              </button>
            </div>
            <button
              className="link danger"
              disabled={busy}
              onClick={() =>
                savedAction(async () => {
                  await api.deleteSavedSearch(x.id);
                  setSaved((xs) => xs?.filter((y) => y.id !== x.id) ?? null);
                })
              }
            >
              {t("src_saved_delete")}
            </button>
          </section>
        ))}
        {error && <p className="error">{error}</p>}
      </Screen>
    );
  }
  const floor = options?.completeness_min ?? 40;
  const completeness = Math.max(filter.completeness_min ?? 0, floor);

  if (view === "results" && page) {
    return (
      <Screen onBack={() => setView("form")}>
        <h1 className="left">{t("src_results_title")}</h1>
        <p className="hint left">{k("src_found", { n: String(page.total) })}</p>
        <button className="link" onClick={() => setView("form")}>
          <Icon name="edit" size={18} /> {t("src_change_filter")}
        </button>
        {items.length === 0 ? (
          <MascotTip tone="calm">{t("src_nothing")}</MascotTip>
        ) : (
          <div className="result-list">
            {items.map((x) => (
              <ResultRow key={x.id} item={x} onOpen={() => onOpen(x.id)} />
            ))}
          </div>
        )}
        <section className="card">
          {savedNow ? (
            <p className="hint small">{t("src_saved_done")}</p>
          ) : (
            <>
              <h2>{t("src_save_title")}</h2>
              <p className="hint small">{items.length === 0 ? t("src_save_hint_empty") : t("src_save_hint")}</p>
              <label className="field">
                {t("src_save_name")}
                <input className="input" maxLength={60} value={saveName} onChange={(e) => setSaveName(e.target.value)} />
              </label>
              <button className="button" disabled={busy} onClick={saveSearch}>
                {t("src_save")}
              </button>
            </>
          )}
        </section>
        {items.length < page.total && (
          <button className="button secondary" disabled={busy} onClick={() => run(items.length)}>
            {t("src_more")}
          </button>
        )}
        {error && <p className="error">{error}</p>}
      </Screen>
    );
  }

  return (
    <Screen>
      <h1 className="left">{t("search_title")}</h1>
      <p className="hint left">{t("src_intro")}</p>
      <div className="buttons">
        <button className="link" onClick={onFavorites}>
          <Icon name="star" size={18} /> {t("fav_title")}
        </button>
        <button className="link" onClick={() => setView("saved")}>
          <Icon name="search" size={18} /> {t("src_saved_title")}
          {saved && saved.length > 0 && ` (${saved.length})`}
        </button>
        <button className="link" onClick={onApplications}>
          <Icon name="send" size={18} /> {t("ap_my_title")}
        </button>
      </div>

      <section className="card">
        <div className="field">
          {t("src_kind")}
          <div className="chips">
            {(["apartment", "house"] as const).map((kind) => {
              const on = filter.kinds?.includes(kind) ?? false;
              return (
                <button key={kind} type="button" className={`chip${on ? " active" : ""}`} aria-pressed={on} onClick={() => patch({ kinds: toggle(filter.kinds ?? [], kind) })}>
                  {k(`src_kind_${kind}`)}
                </button>
              );
            })}
          </div>
        </div>
        <div className="field">
          {t("src_rooms")}
          <div className="chips">
            {ROOMS.map((r) => {
              const on = filter.rooms?.includes(r) ?? false;
              return (
                <button key={r} type="button" className={`chip${on ? " active" : ""}`} aria-pressed={on} onClick={() => patch({ rooms: toggle(filter.rooms ?? [], r) })}>
                  {roomsLabel(r)}
                </button>
              );
            })}
          </div>
        </div>
        <div className="pair">
          <label className="field">
            {t("src_price_min")}
            <input className="input" inputMode="numeric" maxLength={9} value={filter.price_min ?? ""} onChange={(e) => patch({ price_min: numOrNull(e.target.value) })} />
          </label>
          <label className="field">
            {t("src_price_max")}
            <input className="input" inputMode="numeric" maxLength={9} value={filter.price_max ?? ""} onChange={(e) => patch({ price_max: numOrNull(e.target.value) })} />
          </label>
        </div>
        <label className="field">
          {t("src_area_min")}
          <input className="input" inputMode="decimal" maxLength={6} value={filter.area_min ?? ""} onChange={(e) => patch({ area_min: numOrNull(e.target.value) })} />
        </label>
        {options && options.districts.length > 0 && (
          <div className="field">
            {t("src_districts")}
            <div className="chips">
              {options.districts.map((d) => {
                const on = filter.district_ids?.includes(d.id) ?? false;
                return (
                  <button key={d.id} type="button" className={`chip${on ? " active" : ""}`} aria-pressed={on} onClick={() => patch({ district_ids: toggle(filter.district_ids ?? [], d.id) })}>
                    {d.name.replace(/ район$/, "")}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </section>

      <section className="card">
        <h2>{t("src_living")}</h2>
        <div className="chips">
          {(["pets", "children", "registration", "floor_not_first", "floor_not_last"] as const).map((key) => {
            const on = Boolean(filter[key]);
            return (
              <button key={key} type="button" className={`chip${on ? " active" : ""}`} aria-pressed={on} onClick={() => patch({ [key]: !on })}>
                {k(`src_${key}`)}
              </button>
            );
          })}
        </div>
        <h2>{t("src_amenities")}</h2>
        <div className="chips">
          {(options?.amenities ?? []).map((a: Amenity) => {
            const on = filter.amenities?.includes(a) ?? false;
            return (
              <button key={a} type="button" className={`chip${on ? " active" : ""}`} aria-pressed={on} onClick={() => patch({ amenities: toggle(filter.amenities ?? [], a) })}>
                {k(`src_amenity_${a}`)}
              </button>
            );
          })}
        </div>
        <label className="field">
          {completeness <= floor ? t("src_completeness_any") : k("src_completeness_from", { n: String(completeness) })}
          <input
            className="range"
            type="range"
            min={floor}
            max={100}
            step={5}
            value={completeness}
            onChange={(e) => patch({ completeness_min: Number(e.target.value) })}
          />
        </label>
        <p className="hint small">{t("src_completeness_hint")}</p>
      </section>

      {error && <p className="error">{error}</p>}
      <button className="button" disabled={busy || !options} onClick={() => run(0)}>
        <Icon name="search" size={20} />
        {t("src_go")}
      </button>
      <button className="link" onClick={() => setFilter({ ...EMPTY, city_code: filter.city_code })}>
        {t("src_reset")}
      </button>
    </Screen>
  );
}

function ResultRow({ item, onOpen }: { item: ListingSummary; onOpen: () => void }) {
  return (
    <button type="button" className={`result-row${item.actual ? "" : " stale"}`} onClick={onOpen}>
      <span className="result-photo">
        {item.cover_photo_id ? (
          <PhotoImage src={{ kind: "listing", id: item.id }} photo={{ id: item.cover_photo_id, position: 0, width: 4, height: 3 }} mode="cover" />
        ) : (
          <Icon name="photo" size={24} />
        )}
      </span>
      <span className="result-body">
        <span className="result-price">
          {k("ls_rent_month", { rent: money(item.price_amount) })}
          {item.is_favorite && <Icon name="star" size={16} />}
        </span>
        <span className="result-line">
          {roomsText(item.rooms)}
          {item.area && ` · ${t("sqm", { area: item.area })}`}
          {item.utilities_included && ` · ${t("src_utilities_in")}`}
        </span>
        <span className="result-line hint">{placeText(item)}</span>
        <span className="result-meta">
          {item.actual ? (
            <Chip tone={item.completeness >= 75 ? "ok" : "neutral"}>{k("src_filled", { n: String(item.completeness) })}</Chip>
          ) : (
            <Chip tone="warn">{t("fav_stale")}</Chip>
          )}
        </span>
      </span>
    </button>
  );
}

// ------------------------------------------------------------- SRC-CARD: оголошення для орендаря

function answerText(spec: FieldSpec, value: unknown): string | null {
  if (value === null || value === undefined) return null;
  switch (spec.kind) {
    case "choice":
      return k(`pf_${spec.key}__${String(value)}`);
    case "multi":
      return Array.isArray(value) && value.length ? value.map((v) => k(`pf_${spec.key}__${String(v)}`)).join(", ") : null;
    case "bool":
      return value ? t("pf_yes") : t("pf_no");
    case "text":
      return null; // опис показуємо окремо
    case "decimal":
      return Number(value).toLocaleString("uk-UA", { maximumFractionDigits: 2 });
    default:
      return String(value);
  }
}

export function ListingView({
  id,
  onBack,
  onApplication,
}: {
  id: string;
  webApp: WebApp;
  onBack: () => void;
  onApplication: (id: string) => void;
}) {
  const [card, setCard] = useState<ListingCard | null>(null);
  // Відкрита заявка на це оголошення — замість «Відгукнутись» показуємо її.
  const [mine, setMine] = useState<Application | null | undefined>(undefined);
  const [applying, setApplying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listingCard(id)
      .then(setCard)
      .catch((e: unknown) => setError(errorText(e)));
    api
      .myApplications()
      .then((xs) => setMine(xs.find((x) => x.listing.listing_id === id && OPEN_APP.includes(x.status)) ?? null))
      .catch(() => setMine(null));
  }, [id]);

  if (!card) {
    return <Screen onBack={onBack}>{error ? <p className="error">{error}</p> : <p className="hint">{t("loading")}</p>}</Screen>;
  }

  async function favorite() {
    if (!card) return;
    setBusy(true);
    setError(null);
    try {
      if (card.is_favorite) await api.removeFavorite(card.id);
      else await api.addFavorite(card.id);
      setCard({ ...card, is_favorite: !card.is_favorite });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const details = card.details as Record<string, unknown>;
  const sections = PROFILE_SECTIONS.map((s) => ({
    key: s.key,
    rows: s.fields.map((f) => [f.key, answerText(f, details[f.key])] as const).filter(([, v]) => v !== null),
  })).filter((s) => s.rows.length > 0);

  return (
    <Screen onBack={onBack}>
      {card.photos.length > 0 && <PhotoSlider photos={card.photos} source={{ kind: "listing", id: card.id }} />}
      {!card.actual && <MascotTip tone="calm">{t("fav_stale_hint")}</MascotTip>}
      <h1 className="left">{k("ls_rent_month", { rent: money(card.price_amount) })}</h1>
      <p className="hint left">
        {roomsText(card.rooms)}
        {card.area && ` · ${t("sqm", { area: card.area })}`}
        {card.floors_total && ` · ${k("src_floors_total", { n: String(card.floors_total) })}`}
      </p>
      <p className="left">{placeText(card)}</p>
      <p className="hint small left">{t("src_address_hidden")}</p>

      <section className="card">
        <h2>{t("src_terms")}</h2>
        <ul className="terms">
          <li>{card.utilities_included ? t("src_utilities_in") : t("src_utilities_out")}</li>
          <li>{card.deposit_amount != null ? k("src_deposit", { amount: money(card.deposit_amount) }) : t("src_no_deposit")}</li>
          {card.min_term_months != null && <li>{k("src_min_term", { n: String(card.min_term_months) })}</li>}
          <li>{card.available_from ? k("src_available_from", { date: formatDate(card.available_from) }) : t("src_available_now")}</li>
        </ul>
        {card.actual && !card.is_mine && (
          <button className={card.is_favorite ? "button secondary" : "button"} disabled={busy} onClick={favorite}>
            <Icon name="star" size={20} />
            {card.is_favorite ? t("fav_remove") : t("fav_add")}
          </button>
        )}
        {!card.actual && card.is_favorite && (
          <button className="link" disabled={busy} onClick={favorite}>
            {t("fav_remove")}
          </button>
        )}
        {error && <p className="error">{error}</p>}
      </section>

      {card.details.description && (
        <section className="card">
          <h2>{t("pf_section_about")}</h2>
          <p className="prewrap">{card.details.description}</p>
        </section>
      )}

      <div className="stat section-head">
        <h2 style={{ margin: 0, flex: 1 }}>{t("src_profile")}</h2>
        <Chip tone={card.completeness >= 75 ? "ok" : "neutral"}>{k("src_filled", { n: String(card.completeness) })}</Chip>
      </div>
      {sections.map((sec) => (
        <Fold
          key={sec.key}
          id={`listing-view:${sec.key}`}
          title={k(`pf_section_${sec.key}`)}
          summary={sec.rows
            .slice(0, 2)
            .map(([, value]) => value)
            .join(" · ")}
        >
          <dl className="facts">
            {sec.rows.map(([key, value]) => (
              <div key={key} className="fact">
                <dt>{k(`pf_${key}`)}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </Fold>
      ))}

      <section className="card">
        <h2>{t("src_owner")}</h2>
        <p className="row-title">{card.owner.display_name}</p>
        <p className="hint small">{k("src_owner_since", { date: formatDate(card.owner.member_since) })}</p>
        <p className="hint small">{t("src_owner_verified")}</p>
      </section>

      {!card.is_mine && card.actual && (
        <>
          <MascotTip>{t("src_no_money")}</MascotTip>
          {mine ? (
            <button className="button secondary" onClick={() => onApplication(mine.id)}>
              <Icon name="send" size={20} />
              {t("ap_open_application")} · <ApplicationChip status={mine.status} />
            </button>
          ) : (
            <button className="button" disabled={mine === undefined} onClick={() => setApplying(true)}>
              <Icon name="send" size={20} />
              {t("ap_apply")}
            </button>
          )}
          {applying && (
            <ApplySheet
              listingId={card.id}
              onClose={() => setApplying(false)}
              onSent={(sent) => {
                setApplying(false);
                onApplication(sent.id);
              }}
            />
          )}
        </>
      )}
    </Screen>
  );
}

// ------------------------------------------------------------- FAV-LIST: обране

export function FavoritesScreen({ onBack, onOpen }: { onBack: () => void; onOpen: (id: string) => void }) {
  const [items, setItems] = useState<ListingSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .favorites()
      .then(setItems)
      .catch((e: unknown) => setError(errorText(e)));
  }, []);

  return (
    <Screen onBack={onBack}>
      <h1 className="left">{t("fav_title")}</h1>
      {error && <p className="error">{error}</p>}
      {items && items.length === 0 && <MascotTip tone="calm">{t("fav_empty")}</MascotTip>}
      {items && items.length > 0 && (
        <div className="result-list">
          {items.map((x) => (
            <ResultRow key={x.id} item={x} onOpen={() => onOpen(x.id)} />
          ))}
        </div>
      )}
      {!items && !error && <p className="hint">{t("loading")}</p>}
    </Screen>
  );
}
