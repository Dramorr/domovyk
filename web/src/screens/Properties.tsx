// Власник: об'єкти й власність (BOT_FLOWS §3: PRP-LIST, PRP-NEW, PRP-CARD, PRP-UNITS, PRP-OWN).

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { api, ApiFailure, type AddressCandidate, type LeaseSummary, type Ownership, type Photo, type Property, type PropertyCreate, type City } from "../api/client";
import { botLink, DIIA_URL } from "../config";
import { t, type TextKey } from "../i18n";
import { confirm, openLink, openTelegramLink, type WebApp } from "../telegram";
import { Icon } from "../icons";
import {
  Chip,
  errorCode,
  errorField,
  errorText,
  formatAddress,
  formatDate,
  ListRow,
  MascotTip,
  Ring,
  roomsText,
  Screen,
} from "../ui";
import { isOpenLease, LeaseRows, LeaseStatusChip, rentText } from "./Leases";
import { PhotoSlider } from "./Photos";
import { CompletenessBar, PublishHint } from "./PropertyProfile";
import { Stepper, type Step } from "./Stepper";

const OWN_TONE = { unverified: "warn", pending: "info", verified: "ok", rejected: "bad" } as const;

export function OwnershipChip({ status }: { status: Property["ownership_status"] }) {
  return <Chip tone={OWN_TONE[status]}>{t(`own_chip_${status}` as TextKey)}</Chip>;
}

function numberOrNull(raw: string): number | null {
  const value = raw.trim().replace(",", ".");
  if (!value) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function fieldName(field: string | undefined): string {
  const names: Record<string, TextKey> = {
    apartment: "prp_apartment",
    street: "prp_street",
    house_number: "prp_house",
    entrance: "prp_entrance",
    floor: "prp_floor",
    rooms: "prp_rooms",
    area: "prp_area",
  };
  return field && names[field] ? t(names[field]) : (field ?? "");
}

function propertyError(e: unknown): string {
  switch (errorCode(e)) {
    case "duplicate_address":
      return t("prp_duplicate");
    case "property_already_yours":
      return t("prp_already_yours");
    case "has_open_lease":
      return t("prp_delete_has_lease");
    case "phone_required":
      return t("prp_phone_required");
    case "invalid_input":
      return errorField(e) ? t("prp_bad_field", { field: fieldName(errorField(e)) }) : errorText(e);
    default:
      return errorText(e);
  }
}

// ------------------------------------------------------------------ PRP-LIST

/** Головна власника: банер, «Моя власність» зі станом оренди кожної квартири, кнопка «Додати». */
export function PropertyList({
  hero,
  onOpen,
  onNew,
}: {
  hero?: ReactNode;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  const [items, setItems] = useState<Property[] | null>(null);
  const [leases, setLeases] = useState<LeaseSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.properties().then(setItems).catch((e: unknown) => setError(errorText(e)));
    api
      .leases()
      .then((all) => setLeases(all.filter((l) => l.my_role === "landlord" && isOpenLease(l.status))))
      .catch(() => setLeases([]));
  }, []);

  return (
    <Screen>
      {hero}
      <h2 className="section-title">{t("prp_list_title")}</h2>
      {error && <p className="error">{error}</p>}
      {!items && !error && (
        <>
          <div className="skeleton" />
          <div className="skeleton" />
        </>
      )}
      {items && items.length === 0 && (
        <MascotTip>
          <p>{t("prp_list_empty")}</p>
        </MascotTip>
      )}
      {items?.map((p) => {
        const lease = leases.find((l) => l.property_id === p.id);
        return (
          <ListRow
            key={p.id}
            icon={p.kind === "house" ? "home" : "building"}
            title={formatAddress(p)}
            sub={
              lease ? (
                <>
                  <LeaseStatusChip status={lease.status} />
                  <span>{rentText(lease.rent_amount)}</span>
                </>
              ) : (
                <>
                  {p.ownership_status === "verified" ? <Chip tone="neutral">{t("prp_free")}</Chip> : <OwnershipChip status={p.ownership_status} />}
                  <span>
                    {roomsText(p.rooms)}
                    {p.area ? ` · ${t("sqm", { area: p.area })}` : ""}
                  </span>
                </>
              )
            }
            right={lease && lease.unread > 0 ? <span className="count">{lease.unread}</span> : p.completeness && <Ring percent={p.completeness.percent} size={44} />}
            onClick={() => onOpen(p.id)}
          />
        );
      })}
      <div className="home-cta">
        <button className="button accent" onClick={onNew}>
          <Icon name="plus" size={20} />
          {t("prp_add_short")}
        </button>
      </div>
    </Screen>
  );
}

// ------------------------------------------------------------------- PRP-NEW

type Draft = {
  kind: "apartment" | "house";
  address: AddressCandidate | null;
  manual: boolean;
  street: string;
  house: string;
  apartment: string;
  entrance: string;
  floor: string;
  rooms: number;
  area: string;
};

const EMPTY: Draft = {
  kind: "apartment",
  address: null,
  manual: false,
  street: "",
  house: "",
  apartment: "",
  entrance: "",
  floor: "",
  rooms: 1,
  area: "",
};

const DOC_MAX_MB = 10; // як OWNERSHIP_MAX_DOCUMENT_MB на бекенді

type Doc = { blob: Blob; name: string };

/** PRP-NEW: слайдер кроків. Довідку можна додати одразу (тоді перевірка йде сама) або пізніше. */
export function NewProperty({ onBack, onCreated }: { onBack: () => void; onCreated: (p: Property) => void }) {
  const [at, setAt] = useState(0);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<AddressCandidate[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Property | null>(null);
  // «Додати довідку зараз» з підсумку: після вибору файлу повертаємось одразу до підсумку.
  const [backToConfirm, setBackToConfirm] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  // ADR 0008: місто з довідника. Вибір показуємо, лише коли активних міст більше одного.
  const [cities, setCities] = useState<City[]>([]);
  const [city, setCity] = useState<string | undefined>(undefined);

  useEffect(() => {
    api
      .cities()
      .then((all) => {
        const active = all.filter((c) => c.status === "active");
        setCities(active);
        setCity((c) => c ?? active[0]?.code);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [at]);

  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));
  const go = (i: number) => {
    setError(null);
    setAt(i);
  };
  const LAST = 3;
  const afterDoc = () => {
    go(backToConfirm ? LAST : 1);
    setBackToConfirm(false);
  };

  function pick(a: AddressCandidate) {
    set({ address: a, manual: false, street: a.street, house: a.house_number });
    setFound(null);
    go(2);
  }

  async function search() {
    if (query.trim().length < 3 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.searchAddress(query, city);
      setFound(result);
      // Єдиний збіг — не змушуємо тицяти в нього: це і є адреса.
      if (result.length === 1 && result[0]) pick(result[0]);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setError(null);
    if (file.size > DOC_MAX_MB * 1024 * 1024) return setError(t("own_bad_file", { mb: String(DOC_MAX_MB) }));
    // Читаємо одразу: на телефонах Telegram дає файл через тимчасовий доступ.
    let data: ArrayBuffer;
    try {
      data = await file.arrayBuffer();
    } catch {
      return setError(t("own_read_failed"));
    }
    if (data.byteLength === 0) return setError(t("own_read_failed"));
    setDoc({ blob: new Blob([data], { type: "application/pdf" }), name: file.name || "document.pdf" });
  }

  function body(): PropertyCreate {
    const a = draft.address;
    return {
      kind: draft.kind,
      address:
        draft.manual || !a
          ? { city_code: city ?? null, street: draft.street, house_number: draft.house }
          : {
              city_code: a.city_code,
              street: a.street,
              house_number: a.house_number,
              district: a.district ?? null,
              lat: a.lat ?? null,
              lon: a.lon ?? null,
              place_id: a.place_id ?? null,
            },
      apartment: draft.kind === "apartment" ? draft.apartment : null,
      entrance: draft.entrance || null,
      floor: numberOrNull(draft.floor),
      rooms: draft.rooms,
      area: numberOrNull(draft.area),
    };
  }

  async function create() {
    setBusy(true);
    setError(null);
    let prop = created;
    try {
      prop ??= await api.createProperty(body());
      setCreated(prop);
    } catch (e) {
      setError(propertyError(e));
      setBusy(false);
      return;
    }
    if (doc) {
      try {
        await api.uploadDocument(prop.id, doc.blob, doc.name);
        await api.submitOwnership(prop.id);
      } catch (e) {
        // Об'єкт уже є — не створюємо вдруге; довідку можна додати в розділі «Власність».
        setError(t("prp_doc_failed", { error: errorText(e) }));
        setBusy(false);
        return;
      }
    }
    onCreated(prop);
  }

  const summary = formatAddress({
    street: draft.street,
    house_number: draft.house,
    apartment: draft.kind === "apartment" ? draft.apartment : null,
  });
  const choosing = !draft.manual && !!found && found.length > 1;
  const addressReady = draft.manual ? draft.street.trim().length >= 2 && !!draft.house.trim() : !!draft.address && !choosing;

  const steps: Step[] = [
    {
      key: "doc",
      title: t("prp_step_doc_title"),
      text: t("prp_step_doc_text"),
      content: (
        <section className="card">
          <ol className="steps">
            <li>{t("own_step1")}</li>
            <li>{t("prp_doc_order")}</li>
          </ol>
          <button type="button" className={`file-field${doc ? " picked" : ""}`} onClick={() => fileInput.current?.click()}>
            <span className="file-field-icon">
              <Icon name={doc ? "check" : "upload"} size={22} />
            </span>
            <span className="file-field-body">
              <span className="file-field-title">{doc ? doc.name : t("prp_doc_pick")}</span>
              <span className="file-field-sub">
                {doc ? t("prp_doc_replace") : t("own_file_limits", { mb: String(DOC_MAX_MB) })}
              </span>
            </span>
          </button>
          <input ref={fileInput} type="file" accept="application/pdf,.pdf" hidden onChange={onFile} />
        </section>
      ),
      action: doc
        ? { label: t("next"), onClick: afterDoc }
        : { label: t("wiz_skip"), secondary: true, onClick: afterDoc },
    },
    {
      key: "address",
      title: t("prp_step_address_title"),
      content: (
        <>
          <div className="segmented" role="radiogroup" aria-label={t("prp_kind_question")}>
            {(["apartment", "house"] as const).map((kind) => (
              <button
                key={kind}
                type="button"
                role="radio"
                aria-checked={draft.kind === kind}
                className={draft.kind === kind ? "active" : ""}
                onClick={() => set({ kind })}
              >
                <Icon name={kind === "apartment" ? "building" : "home"} size={20} />
                {t(kind === "apartment" ? "prp_kind_apartment_plain" : "prp_kind_house_plain")}
              </button>
            ))}
          </div>

          {draft.manual ? (
            <section className="card">
              <p className="hint small">{t("prp_manual_hint")}</p>
              <label className="field">
                {t("prp_street")}
                <input className="input" value={draft.street} maxLength={100} onChange={(e) => set({ street: e.target.value, address: null })} />
              </label>
              <label className="field">
                {t("prp_house")}
                <input className="input" value={draft.house} maxLength={20} onChange={(e) => set({ house: e.target.value, address: null })} />
              </label>
              <button className="button ghost small" onClick={() => set({ manual: false })}>
                <Icon name="search" size={18} />
                {t("prp_back_to_search")}
              </button>
            </section>
          ) : (
            <section className="card">
              {cities.length > 1 && (
                <div className="field">
                  {t("prp_city")}
                  <div className="chips">
                    {cities.map((c) => (
                      <button
                        key={c.code}
                        className={`chip${c.code === city ? " active" : ""}`}
                        aria-pressed={c.code === city}
                        onClick={() => {
                          setCity(c.code);
                          setFound(null);
                        }}
                      >
                        {c.name}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <label className="field" htmlFor="prp-search">
                {t("prp_street_label")}
              </label>
              <div className={`search-field${found && found.length > 1 ? " open" : ""}`}>
                <input
                  id="prp-search"
                  className="input"
                  type="search"
                  enterKeyHint="search"
                  value={query}
                  maxLength={120}
                  placeholder={t("prp_street_placeholder")}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setFound(null);
                  }}
                  onKeyDown={(e) => e.key === "Enter" && void search()}
                />
                <button
                  type="button"
                  className="search-go"
                  aria-label={t("prp_search")}
                  disabled={busy || query.trim().length < 3}
                  onClick={() => void search()}
                >
                  {busy ? <span className="spinner" /> : <Icon name="search" size={20} />}
                </button>
                {found && found.length > 1 && (
                  <div className="suggest" role="listbox" aria-label={t("prp_found_question")}>
                    {found.map((a) => (
                      <button
                        key={`${a.street}|${a.house_number}|${a.place_id ?? ""}`}
                        role="option"
                        aria-selected={draft.address === a}
                        className="suggest-item"
                        onClick={() => pick(a)}
                      >
                        <span className="row-title">{formatAddress(a)}</span>
                        {a.district && <span className="row-sub">{a.district}</span>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {found && found.length > 1 && <p className="hint small">{t("prp_found_question")}</p>}
              {found && found.length === 0 && <p className="hint">{t("prp_not_found")}</p>}
              {draft.address && !found && (
                <p className="picked-address">
                  <Icon name="check" size={18} />
                  {formatAddress(draft.address)}
                </p>
              )}
              <button
                className="button ghost small"
                onClick={() => set({ manual: true, address: null, street: draft.address ? draft.street : "", house: draft.address ? draft.house : "" })}
              >
                <Icon name="pen" size={18} />
                {t("prp_manual")}
              </button>
            </section>
          )}
        </>
      ),
      action: addressReady ? { label: t("next"), onClick: () => go(2) } : undefined,
    },
    {
      key: "details",
      title: t("prp_step_details_title"),
      content: (
        <section className="card">
          <p className="row-title">{formatAddress({ street: draft.street, house_number: draft.house })}</p>
          {draft.kind === "apartment" && (
            <label className="field">
              {t("prp_apartment")}
              <input className="input" value={draft.apartment} maxLength={10} onChange={(e) => set({ apartment: e.target.value })} />
            </label>
          )}
          <div className="pair">
            <label className="field">
              {t("prp_entrance")} <span className="hint">({t("prp_optional")})</span>
              <input className="input" value={draft.entrance} maxLength={10} onChange={(e) => set({ entrance: e.target.value })} />
            </label>
            <label className="field">
              {t("prp_floor")} <span className="hint">({t("prp_optional")})</span>
              <input className="input" inputMode="numeric" value={draft.floor} maxLength={3} onChange={(e) => set({ floor: e.target.value })} />
            </label>
          </div>
          <p className="field">{t("prp_rooms")}</p>
          <div className="segmented rooms" role="radiogroup" aria-label={t("prp_rooms")}>
            {[0, 1, 2, 3, 4, 5].map((n) => (
              <button
                key={n}
                type="button"
                role="radio"
                aria-checked={draft.rooms === n}
                className={draft.rooms === n ? "active" : ""}
                onClick={() => set({ rooms: n })}
              >
                {n === 0 ? t("rooms_studio") : n === 5 ? "5+" : n}
              </button>
            ))}
          </div>
          <label className="field">
            {t("prp_area")} <span className="hint">({t("prp_optional")})</span>
            <input className="input" inputMode="decimal" value={draft.area} maxLength={7} onChange={(e) => set({ area: e.target.value })} />
          </label>
        </section>
      ),
      action: { label: t("next"), disabled: draft.kind === "apartment" && !draft.apartment.trim(), onClick: () => go(LAST) },
    },
    {
      key: "confirm",
      title: t("prp_confirm_title"),
      content: (
        <section className="card">
          <div className="summary-head">
            <span className="row-icon">
              <Icon name={draft.kind === "house" ? "home" : "building"} />
            </span>
            <div>
              <p className="row-title">{summary}</p>
              <p className="hint small">
                {roomsText(draft.rooms)}
                {draft.area && ` · ${t("sqm", { area: draft.area })}`}
                {draft.entrance && ` · ${t("prp_entrance")} ${draft.entrance}`}
                {draft.floor && ` · ${t("prp_floor")} ${draft.floor}`}
              </p>
            </div>
          </div>
          <p className={`summary-doc${doc ? " ok" : ""}`}>
            <Icon name={doc ? "doc" : "alert"} size={18} />
            <span>{doc ? t("prp_confirm_doc", { name: doc.name }) : t("prp_confirm_no_doc")}</span>
          </p>
          {!doc && !created && (
            <button
              className="button ghost small"
              onClick={() => {
                setBackToConfirm(true);
                go(0);
              }}
            >
              <Icon name="upload" size={18} />
              {t("prp_doc_add_now")}
            </button>
          )}
        </section>
      ),
      action: created
        ? { label: t("prp_open_property"), final: true, onClick: () => onCreated(created) }
        : { label: busy ? t("saving") : t("prp_create"), final: true, icon: "check", disabled: busy, onClick: () => void create() },
    },
  ];

  return <Stepper steps={steps} at={at} onGo={go} onExit={onBack} title={t("prp_new_title")} error={error} />;
}

// ------------------------------------------------------------------ PRP-CARD

export function PropertyCard({
  id,
  webApp,
  onBack,
  onOwnership,
  onProfile,
  onNewLease,
  onOpenLease,
  onChat,
  onDeleted,
}: {
  id: string;
  webApp: WebApp;
  onBack: () => void;
  onOwnership: () => void;
  onProfile: () => void;
  onNewLease: (prop: Property) => void;
  onOpenLease: (leaseId: string) => void;
  onChat: (leaseId: string) => void;
  onDeleted: () => void;
}) {
  const [prop, setProp] = useState<Property | null>(null);
  // Оренди цієї квартири: поточна (незавершена) і історія.
  const [leases, setLeases] = useState<LeaseSummary[] | null>(null);
  const [photos, setPhotos] = useState<Photo[] | null>(null);
  const [edit, setEdit] = useState(false);
  const [form, setForm] = useState({ entrance: "", floor: "", rooms: 1, area: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.property(id).then(setProp).catch((e: unknown) => setError(errorText(e)));
    api
      .photos(id)
      .then(setPhotos)
      .catch(() => setPhotos([]));
    api
      .leases()
      .then((all) => setLeases(all.filter((l) => l.property_id === id && l.my_role === "landlord")))
      .catch(() => setLeases([]));
  }, [id]);

  async function run(action: () => Promise<Property | void>) {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      if (result) setProp(result);
    } catch (e) {
      setError(propertyError(e));
    } finally {
      setBusy(false);
    }
  }

  if (!prop) {
    return (
      <Screen onBack={onBack}>
        {error ? <p className="error">{error}</p> : <p className="hint">{t("loading")}</p>}
      </Screen>
    );
  }

  const startEdit = () => {
    setForm({
      entrance: prop.entrance ?? "",
      floor: prop.floor?.toString() ?? "",
      rooms: prop.rooms,
      area: prop.area ?? "",
    });
    setEdit(true);
  };

  async function remove() {
    const question = prop?.ownership_status === "pending" ? "prp_delete_confirm_pending" : "prp_delete_confirm";
    if (!(await confirm(webApp, t(question)))) return;
    await run(async () => {
      await api.deleteProperty(id);
      onDeleted();
    });
  }

  const current = leases?.find((l) => isOpenLease(l.status)) ?? null;
  const history = leases?.filter((l) => !isOpenLease(l.status)) ?? [];

  return (
    <Screen onBack={onBack}>
      {photos && photos.length > 0 && <PhotoSlider photos={photos} source={{ kind: "property", id }} />}
      {photos && photos.length === 0 && (
        <button type="button" className="photo-empty" onClick={onProfile}>
          <Icon name="photo" size={28} />
          <span>{t("prp_card_add_photos")}</span>
        </button>
      )}
      <h1 className="left">{formatAddress(prop)}</h1>
      <p className="hint left">
        {roomsText(prop.rooms)}
        {prop.area && ` · ${t("sqm", { area: prop.area })}`}
        {prop.district && ` · ${prop.district}`}
      </p>

      <LeaseBlock
        prop={prop}
        current={current}
        onNew={() => onNewLease(prop)}
        onOpen={onOpenLease}
        onChat={onChat}
      />

      <section className="card">
        <h2>{t("pf_title")}</h2>
        <CompletenessBar p={prop} />
        <PublishHint p={prop} />
        <button className="button secondary" onClick={onProfile}>
          <Icon name="edit" size={20} />
          {t("pf_open_plain")}
        </button>
      </section>

      <section className="card">
        <div className="stat">
          <h2 style={{ margin: 0, flex: 1 }}>{t("prp_card_ownership")}</h2>
          <OwnershipChip status={prop.ownership_status} />
        </div>
        {prop.ownership_status === "pending" ? (
          <p className="hint small">{t("prp_card_pending_hint")}</p>
        ) : (
          prop.ownership_status !== "verified" && <p className="hint small">{t("prp_card_verify_hint")}</p>
        )}
        <button className={prop.ownership_status === "verified" ? "button secondary" : "button"} onClick={onOwnership}>
          <Icon name="shield" size={20} />
          {prop.ownership_status === "unverified" ? t("prp_card_verify") : t("prp_card_verify_status")}
        </button>
      </section>

      {history.length > 0 && (
        <section className="card">
          <h2>{t("prp_card_history")}</h2>
          <LeaseRows
            items={history}
            webApp={webApp}
            showAddress={false}
            onOpen={onOpenLease}
            onHidden={(lid) => setLeases((xs) => xs?.filter((x) => x.id !== lid) ?? null)}
          />
        </section>
      )}

      <section className="card">
        <h2>{t("prp_details")}</h2>
        {!edit ? (
          <>
            <p className="small">
              {prop.entrance && `${t("prp_entrance")}: ${prop.entrance} · `}
              {prop.floor !== null && prop.floor !== undefined && `${t("prp_floor")}: ${prop.floor} · `}
              {t("prp_rooms")}: {prop.rooms === 0 ? t("rooms_studio") : prop.rooms}
              {prop.area && ` · ${t("sqm", { area: prop.area })}`}
            </p>
            <button className="link" onClick={startEdit}>
              {t("edit")}
            </button>
          </>
        ) : (
          <>
            <div className="pair">
              <label className="field">
                {t("prp_entrance")}
                <input className="input" value={form.entrance} maxLength={10} onChange={(e) => setForm({ ...form, entrance: e.target.value })} />
              </label>
              <label className="field">
                {t("prp_floor")}
                <input className="input" inputMode="numeric" value={form.floor} maxLength={3} onChange={(e) => setForm({ ...form, floor: e.target.value })} />
              </label>
            </div>
            <div className="pair">
              <label className="field">
                {t("prp_rooms")}
                <input className="input" type="number" min={0} max={30} value={form.rooms} onChange={(e) => setForm({ ...form, rooms: Number(e.target.value) })} />
              </label>
              <label className="field">
                {t("prp_area")}
                <input className="input" inputMode="decimal" value={form.area} maxLength={7} onChange={(e) => setForm({ ...form, area: e.target.value })} />
              </label>
            </div>
            <button
              className="button"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const updated = await api.updateProperty(id, {
                    entrance: form.entrance || null,
                    floor: numberOrNull(form.floor),
                    rooms: form.rooms,
                    area: numberOrNull(form.area),
                  });
                  setEdit(false);
                  return updated;
                })
              }
            >
              {t("save")}
            </button>
            <button className="link" onClick={() => setEdit(false)}>
              {t("cancel")}
            </button>
          </>
        )}
      </section>

      {error && <p className="error">{error}</p>}
      <button className="link danger" onClick={remove} disabled={busy}>
        {t("prp_delete")}
      </button>
    </Screen>
  );
}

/** Блок «Оренда» в картці квартири: поточна оренда або як здати. */
function LeaseBlock({
  prop,
  current,
  onNew,
  onOpen,
  onChat,
}: {
  prop: Property;
  current: LeaseSummary | null;
  onNew: () => void;
  onOpen: (id: string) => void;
  onChat: (id: string) => void;
}) {
  if (current) {
    return (
      <section className="card accent">
        <div className="stat">
          <h2 style={{ margin: 0, flex: 1 }}>{t("prp_card_lease")}</h2>
          <LeaseStatusChip status={current.status} />
        </div>
        <p className="row-title">{rentText(current.rent_amount)}</p>
        <p className="hint small">{t("ls_period", { from: formatDate(current.start_date), to: formatDate(current.end_date) })}</p>
        <div className="buttons">
          <button className="button" onClick={() => onOpen(current.id)}>
            <Icon name="key" size={20} />
            {t("prp_card_lease_open")}
          </button>
          <button className="button secondary" onClick={() => onChat(current.id)}>
            <Icon name="send" size={20} />
            {t("prp_card_lease_chat")}
            {current.unread > 0 && <span className="count">{current.unread}</span>}
          </button>
        </div>
      </section>
    );
  }
  return (
    <section className="card">
      <div className="stat">
        <h2 style={{ margin: 0, flex: 1 }}>{t("prp_card_lease")}</h2>
        <Chip tone="neutral">{t("prp_free")}</Chip>
      </div>
      {prop.ownership_status === "verified" ? (
        <>
          <p className="hint small">{t("prp_card_free_hint")}</p>
          <button className="button" onClick={onNew}>
            <Icon name="key" size={20} />
            {t("ls_new_from_property")}
          </button>
        </>
      ) : (
        <p className="hint small">{t("prp_card_lease_wait_own")}</p>
      )}
    </section>
  );
}

// ------------------------------------------------------------------- PRP-OWN

export function OwnershipScreen({ id, webApp, onBack }: { id: string; webApp: WebApp; onBack: () => void }) {
  const [prop, setProp] = useState<Property | null>(null);
  const [own, setOwn] = useState<Ownership | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    Promise.all([api.property(id), api.ownership(id)])
      .then(([p, o]) => {
        setProp(p);
        setOwn(o);
      })
      .catch((e: unknown) => setError(errorText(e)));
  }, [id]);

  useEffect(load, [load]);

  async function run(action: () => Promise<Ownership>) {
    setBusy(true);
    setError(null);
    try {
      setOwn(await action());
    } catch (e) {
      const code = errorCode(e);
      if (e instanceof ApiFailure && e.kind === "network") setError(t("own_upload_failed"));
      else if (code === "too_many_documents") setError(t("own_too_many"));
      else if (code === "invalid_input" && errorField(e) === "file")
        setError(t("own_bad_file", { mb: String(own?.max_document_mb ?? 10) }));
      else setError(errorText(e));
      load();
    } finally {
      setBusy(false);
    }
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (own && file.size > own.max_document_mb * 1024 * 1024) {
      setError(t("own_bad_file", { mb: String(own.max_document_mb) }));
      return;
    }
    // Спершу читаємо файл повністю: на телефонах Telegram дає файл через тимчасовий
    // доступ, і потокове читання під час відправки може обірватися ще до сервера.
    let data: ArrayBuffer;
    try {
      data = await file.arrayBuffer();
    } catch {
      setError(t("own_read_failed"));
      return;
    }
    if (data.byteLength === 0) {
      setError(t("own_read_failed"));
      return;
    }
    const blob = new Blob([data], { type: "application/pdf" });
    await run(() => api.uploadDocument(id, blob, file.name || "document.pdf"));
  }

  if (!prop || !own) {
    return (
      <Screen onBack={onBack}>
        {error ? <p className="error">{error}</p> : <p className="hint">{t("loading")}</p>}
      </Screen>
    );
  }

  const v = own.verification;
  const collecting = own.ownership_status === "unverified" || own.ownership_status === "rejected";
  // Після відмови нова спроба = нова чернетка; документи відхиленої не показуємо як поточні.
  const draftDocs = v && v.status === "draft" ? v.documents : [];
  const bot = botLink(`own_${id}`);

  return (
    <Screen onBack={onBack}>
      <h1>{t("own_title")}</h1>
      <p className="hint">{formatAddress(prop)}</p>

      {own.ownership_status === "verified" && <p className="ok">{t("own_verified")}</p>}
      {own.ownership_status === "pending" && <p>{t("own_pending")}</p>}
      {own.ownership_status === "rejected" && v?.status === "rejected" && (
        <p className="error">{t("own_rejected", { reason: v.reject_reason ?? "" })}</p>
      )}

      {collecting && (
        <>
          <MascotTip>
            <p>{t("own_intro")}</p>
          </MascotTip>
          <section className="card">
            <ol className="steps">
              <li>{t("own_step1")}</li>
              <li>{t("own_step2")}</li>
              <li>{t("own_step3")}</li>
            </ol>
            <p className="hint small">{t("own_extra")}</p>
            <button className="link" onClick={() => openLink(webApp, DIIA_URL)}>
              <Icon name="link" size={18} /> {t("own_open_diia")}
            </button>
          </section>

          <section className="card">
            <h2>{t("own_files", { count: String(draftDocs.length), max: String(own.max_documents) })}</h2>
            {draftDocs.map((d) => (
              <p key={d.id} className="small unit">
                <span className="with-icon">
                  <Icon name="doc" size={18} />
                  {d.filename}
                </span>
                <button className="link" disabled={busy} onClick={() => run(() => api.removeDocument(id, d.id))}>
                  {t("delete")}
                </button>
              </p>
            ))}
            <input ref={fileInput} type="file" accept="application/pdf,.pdf" hidden onChange={onFile} />
            <button
              className="button secondary"
              disabled={busy || draftDocs.length >= own.max_documents}
              onClick={() => fileInput.current?.click()}
            >
              <Icon name="upload" size={20} />
              {busy ? t("own_uploading") : t("own_add_file")}
            </button>
            <p className="hint small">{t("own_file_limits", { mb: String(own.max_document_mb) })}</p>
            {draftDocs.length > 0 && (
              <button className="button" disabled={busy} onClick={() => run(() => api.submitOwnership(id))}>
                <Icon name="send" size={20} />
                {t("own_submit")}
              </button>
            )}
            {bot && (
              <p className="small">
                <button className="link" onClick={() => openTelegramLink(webApp, bot)}>
                  {t("own_via_bot")}
                </button>
              </p>
            )}
          </section>

          {own.dev_bypass_available && (
            <section className="card dev">
              <p className="hint small">{t("own_dev_hint")}</p>
              <button className="button secondary" disabled={busy} onClick={() => run(() => api.devVerify(id))}>
                {t("own_dev_verify")}
              </button>
            </section>
          )}
        </>
      )}

      {v && v.status === "pending" && (
        <section className="card">
          <h2>{t("own_files", { count: String(v.documents.length), max: String(own.max_documents) })}</h2>
          {v.documents.map((d) => (
            <p key={d.id} className="small">
              📄 {d.filename} · {formatDate(d.uploaded_at)}
            </p>
          ))}
        </section>
      )}

      {error && <p className="error">{error}</p>}
    </Screen>
  );
}
