// Пошук для орендаря (M6, BOT_FLOWS §5, FR-SRC-1..6). Спершу житло міста, новіші спершу;
// фільтри — окремою сторінкою, глобальні й зберігаються на пристрої одразу (FR-SRC-1).
// Адреса — вулиця й будинок; квартира, під'їзд і поверх — після схвалення заявки
// (FR-LST-4, ADR 0021).

import { useEffect, useState } from "react";

import { api, ApiFailure, type Amenity, type Application, type ListingCard, type ListingSummary, type SavedSearch, type SearchIn, type SearchOptions, type SearchPage } from "../api/client";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import { PROFILE_SECTIONS, type FieldSpec } from "../profileSpec";
import { type WebApp } from "../telegram";
import { Chip, errorText, Fold, formatAddress, formatDate, MascotTip, roomsText, Screen } from "../ui";
import { ApplicationChip, ApplySheet } from "./Applications";
import { PhotoSlider } from "./Photos";

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

const LIVING = ["pets", "children", "registration", "floor_not_first", "floor_not_last"] as const;

/** Лічильник вибраного в заголовку акордеона; нуль не показуємо. */
function Picked({ n }: { n: number }) {
  return n > 0 ? <span className="count">{n}</span> : null;
}

function roomsLabel(r: number): string {
  if (r === 0) return t("rooms_studio");
  return r === 4 ? "4+" : String(r);
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

// ------------------------------------------------------------- SRC: житло, фільтри, мої пошуки

// Екран розмонтовується, коли відкриваємо картку; повертаючись, людина чекає ті самі результати.
type View = "results" | "filter" | "saved";
type Session = { key: string; page: SearchPage; items: ListingSummary[] };
let lastSession: Session | null = null;

const filterKey = (f: Filter) => JSON.stringify(f);

/** Скільки умов вибрано: для лічильника на кнопці «Фільтри» і в заголовках акордеонів. */
function counts(f: Filter, floor: number) {
  const place = (f.kinds?.length ?? 0) + (f.rooms?.length ?? 0) + (f.area_min != null ? 1 : 0);
  const price = (f.price_min != null ? 1 : 0) + (f.price_max != null ? 1 : 0);
  const districts = f.district_ids?.length ?? 0;
  const living = LIVING.filter((key) => Boolean(f[key])).length;
  const amenities = f.amenities?.length ?? 0;
  const profile = (f.completeness_min ?? 0) > floor ? 1 : 0;
  return { place, price, districts, living, amenities, profile, total: place + price + districts + living + amenities + profile };
}

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
  const [shownKey, setShownKey] = useState<string | null>(lastSession?.key ?? null);
  const [view, setView] = useState<View>(initialView ?? "results");
  const [saved, setSaved] = useState<SavedSearch[] | null>(null);
  const [savedNow, setSavedNow] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Фільтр — глобальний: кожна зміна одразу лягає на пристрій (це локальний запис, без мережі).
  useEffect(() => saveFilter(filter), [filter]);

  useEffect(() => {
    lastSession = page && shownKey ? { key: shownKey, page, items } : null;
  }, [page, items, shownKey]);

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
      if (offset === 0) {
        setShownKey(filterKey(f));
        setSavedNow(false);
        setSaveName(filterSummary(f, options));
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  // Житло показуємо одразу: без фільтра — усе в місті, новіші спершу. Перезапит — лише коли
  // фільтр змінився відтоді, як ми показували результати.
  useEffect(() => {
    if (view === "results" && shownKey !== filterKey(filter)) void run(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

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

  const floor = options?.completeness_min ?? 40;
  const completeness = Math.max(filter.completeness_min ?? 0, floor);
  const c = counts(filter, floor);

  if (view === "saved") {
    return (
      <Screen onBack={() => setView("results")}>
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
                  setFilter({ ...EMPTY, ...x.criteria });
                  setView("results");
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

  if (view === "filter") {
    return (
      <Screen onBack={() => setView("results")}>
        <div className="title-row">
          <h1 className="left">{t("src_filter_title")}</h1>
          <button type="button" className="pill-button" disabled={c.total === 0} onClick={() => setFilter({ ...EMPTY, city_code: filter.city_code })}>
            <Icon name="close" size={18} />
            {t("src_reset")}
          </button>
        </div>
        <p className="hint left">{t("src_filter_intro")}</p>

        <Fold id="search:place" icon="home" title={t("src_fold_place")} aside={<Picked n={c.place} />} defaultOpen>
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
          <label className="field">
            {t("src_area_min")}
            <input className="input" inputMode="decimal" maxLength={6} value={filter.area_min ?? ""} onChange={(e) => patch({ area_min: numOrNull(e.target.value) })} />
          </label>
        </Fold>

        <Fold id="search:price" icon="key" title={t("src_fold_price")} aside={<Picked n={c.price} />} defaultOpen>
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
        </Fold>

        {options && options.districts.length > 0 && (
          <Fold id="search:districts" icon="building" title={t("src_districts")} aside={<Picked n={c.districts} />}>
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
          </Fold>
        )}

        <Fold id="search:living" icon="user" title={t("src_living")} aside={<Picked n={c.living} />}>
          <div className="chips">
            {LIVING.map((key) => {
              const on = Boolean(filter[key]);
              return (
                <button key={key} type="button" className={`chip${on ? " active" : ""}`} aria-pressed={on} onClick={() => patch({ [key]: !on })}>
                  {k(`src_${key}`)}
                </button>
              );
            })}
          </div>
        </Fold>

        <Fold id="search:amenities" icon="check" title={t("src_amenities")} aside={<Picked n={c.amenities} />}>
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
        </Fold>

        <Fold id="search:completeness" icon="doc" title={t("src_fold_profile")} aside={<Picked n={c.profile} />}>
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
        </Fold>

        <div className="sticky-spacer" />
        <div className="sticky-action">
          <button className="button" onClick={() => setView("results")}>
            <Icon name="check" size={20} />
            {t("src_go")}
            {c.total > 0 && <span className="count">{c.total}</span>}
          </button>
        </div>
      </Screen>
    );
  }

  return (
    <Screen>
      <h1 className="left">{t("search_title")}</h1>
      <p className="hint left">{t("src_intro")}</p>
      <div className="chips quick-links">
        <button type="button" className="chip" onClick={onFavorites}>
          <Icon name="heart" size={16} /> {t("fav_title")}
        </button>
        <button type="button" className="chip" onClick={onApplications}>
          <Icon name="send" size={16} /> {t("ap_my_title")}
        </button>
      </div>

      {!page ? (
        error ? null : <p className="hint">{t("loading")}</p>
      ) : (
        <>
          <p className="hint left small">{k(c.total > 0 ? "src_found" : "src_found_all", { n: String(page.total) })}</p>
          {items.length === 0 ? (
            <MascotTip tone="calm">{c.total > 0 ? t("src_nothing") : t("src_nothing_all")}</MascotTip>
          ) : (
            <div className="result-list">
              {items.map((x) => (
                <ResultCard key={x.id} item={x} onOpen={() => onOpen(x.id)} onChange={(y) => setItems((xs) => xs.map((z) => (z.id === y.id ? y : z)))} />
              ))}
            </div>
          )}
          {items.length < page.total && (
            <button className="button secondary" disabled={busy} onClick={() => run(items.length)}>
              {t("src_more")}
            </button>
          )}
          {c.total > 0 && (
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
          )}
        </>
      )}
      {error && <p className="error">{error}</p>}

      <div className="float-spacer" />
      <nav className="float-bar" aria-label={t("src_filters")}>
        <button type="button" onClick={() => setView("saved")} aria-label={t("src_saved_title")}>
          <Icon name="search" size={20} />
          {saved && saved.length > 0 && <span className="count">{saved.length}</span>}
        </button>
        <span className="float-sep" aria-hidden />
        <button type="button" onClick={() => setView("filter")}>
          <Icon name="filter" size={20} />
          {t("src_filters")}
          {c.total > 0 && <span className="count">{c.total}</span>}
        </button>
      </nav>
    </Screen>
  );
}

function addressText(x: Pick<ListingSummary, "street" | "house_number">): string {
  return formatAddress({ street: x.street, house_number: x.house_number });
}

/** Серце «в обране» поверх фото; ставить і знімає без переходу на картку (FR-SRC-4). */
function FavoriteToggle({ item, onChange }: { item: ListingSummary; onChange: (x: ListingSummary) => void }) {
  const [busy, setBusy] = useState(false);
  if (!item.actual && !item.is_favorite) return null;
  async function toggleFavorite() {
    setBusy(true);
    try {
      if (item.is_favorite) await api.removeFavorite(item.id);
      else await api.addFavorite(item.id);
      onChange({ ...item, is_favorite: !item.is_favorite });
    } catch {
      // Не вийшло — серце лишається як було; людина просто натисне ще раз.
    } finally {
      setBusy(false);
    }
  }
  return (
    <button
      type="button"
      className={`fav-toggle${item.is_favorite ? " on" : ""}`}
      aria-pressed={item.is_favorite}
      aria-label={item.is_favorite ? t("fav_remove") : t("fav_add")}
      disabled={busy}
      onClick={toggleFavorite}
    >
      <Icon name="heart" size={22} filled={item.is_favorite} />
    </button>
  );
}

function ResultCard({ item, onOpen, onChange }: { item: ListingSummary; onOpen: () => void; onChange: (x: ListingSummary) => void }) {
  return (
    <article className={`listing-card${item.actual ? "" : " stale"}`}>
      <div className="lc-media">
        {item.photos.length > 0 ? (
          <PhotoSlider photos={item.photos} source={{ kind: "listing", id: item.id }} onTap={onOpen} />
        ) : (
          <button type="button" className="lc-nophoto" onClick={onOpen} aria-label={addressText(item)}>
            <Icon name="photo" size={32} />
          </button>
        )}
        <div className="lc-over">
          <span className="lc-price">{k("ls_rent_month", { rent: money(item.price_amount) })}</span>
          <span className="lc-address">{addressText(item)}</span>
        </div>
        <FavoriteToggle item={item} onChange={onChange} />
      </div>
      <button type="button" className="lc-body" onClick={onOpen}>
        <span className="lc-chips">
          {item.district && <span className="lc-chip">{item.district.replace(/ район$/, "")}</span>}
          <span className="lc-chip">{item.city}</span>
          {item.utilities_included && <span className="lc-chip">{t("src_utilities_in")}</span>}
        </span>
        <span className="lc-line">
          {roomsText(item.rooms)}
          {item.area && ` · ${t("sqm", { area: item.area })}`}
        </span>
        <span className="lc-meta">
          {item.actual ? (
            <Chip tone={item.completeness >= 75 ? "ok" : "neutral"}>{k("src_filled", { n: String(item.completeness) })}</Chip>
          ) : (
            <Chip tone="warn">{t("fav_stale")}</Chip>
          )}
        </span>
      </button>
    </article>
  );
}

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

  const details = card.details as Record<string, unknown>;
  const sections = PROFILE_SECTIONS.map((s) => ({
    key: s.key,
    rows: s.fields.map((f) => [f.key, answerText(f, details[f.key])] as const).filter(([, v]) => v !== null),
  })).filter((s) => s.rows.length > 0);

  return (
    <Screen onBack={onBack}>
      {card.photos.length > 0 ? (
        <div className="lv-media">
          <PhotoSlider photos={card.photos} source={{ kind: "listing", id: card.id }} />
          {!card.is_mine && <FavoriteToggle item={card} onChange={(x) => setCard({ ...card, is_favorite: x.is_favorite })} />}
        </div>
      ) : null}
      {!card.actual && <MascotTip tone="calm">{t("fav_stale_hint")}</MascotTip>}
      <div className="title-row">
        <h1 className="left">{addressText(card)}</h1>
        {card.photos.length === 0 && !card.is_mine && (
          <span className="lv-fav-inline">
            <FavoriteToggle item={card} onChange={(x) => setCard({ ...card, is_favorite: x.is_favorite })} />
          </span>
        )}
      </div>
      <p className="lv-price">{k("ls_rent_month", { rent: money(card.price_amount) })}</p>
      <p className="hint left">
        {roomsText(card.rooms)}
        {card.area && ` · ${t("sqm", { area: card.area })}`}
        {card.floors_total && ` · ${k("src_floors_total", { n: String(card.floors_total) })}`}
      </p>
      <p className="left">{[card.district, card.city].filter(Boolean).join(" · ")}</p>
      <p className="hint small left">{t("src_address_hidden")}</p>

      <section className="card">
        <h2>{t("src_terms")}</h2>
        <ul className="terms">
          <li>{card.utilities_included ? t("src_utilities_in") : t("src_utilities_out")}</li>
          <li>{card.deposit_amount != null ? k("src_deposit", { amount: money(card.deposit_amount) }) : t("src_no_deposit")}</li>
          {card.min_term_months != null && <li>{k("src_min_term", { n: String(card.min_term_months) })}</li>}
          <li>{card.available_from ? k("src_available_from", { date: formatDate(card.available_from) }) : t("src_available_now")}</li>
        </ul>
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
            <ResultCard key={x.id} item={x} onOpen={() => onOpen(x.id)} onChange={(y) => setItems((xs) => xs?.map((z) => (z.id === y.id ? y : z)) ?? null)} />
          ))}
        </div>
      )}
      {!items && !error && <p className="hint">{t("loading")}</p>}
    </Screen>
  );
}
