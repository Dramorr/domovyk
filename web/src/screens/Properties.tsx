// Власник: об'єкти й власність (BOT_FLOWS §3: PRP-LIST, PRP-NEW, PRP-CARD, PRP-UNITS, PRP-OWN).

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiFailure, type AddressCandidate, type Ownership, type Property, type PropertyCreate, type City } from "../api/client";
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
  Mascot,
  MascotTip,
  Ring,
  roomsText,
  Screen,
} from "../ui";
import { CompletenessBar, PublishHint } from "./PropertyProfile";

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

export function PropertyList({
  onBack,
  onOpen,
  onNew,
}: {
  onBack?: () => void;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  const [items, setItems] = useState<Property[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.properties().then(setItems).catch((e: unknown) => setError(errorText(e)));
  }, []);

  return (
    <Screen onBack={onBack}>
      <h1>{t("prp_list_title")}</h1>
      {error && <p className="error">{error}</p>}
      {!items && !error && (
        <>
          <div className="skeleton" />
          <div className="skeleton" />
        </>
      )}
      {items && items.length === 0 && (
        <>
          <Mascot size="md" />
          <MascotTip>
            <p>{t("prp_list_empty")}</p>
          </MascotTip>
        </>
      )}
      {items?.map((p) => (
        <ListRow
          key={p.id}
          icon={p.kind === "house" ? "home" : "building"}
          title={formatAddress(p)}
          sub={
            <>
              <OwnershipChip status={p.ownership_status} />
              <span>
                {roomsText(p.rooms)}
                {p.area ? ` · ${t("sqm", { area: p.area })}` : ""}
              </span>
            </>
          }
          right={p.completeness && <Ring percent={p.completeness.percent} size={44} />}
          onClick={() => onOpen(p.id)}
        />
      ))}
      <button className="button" onClick={onNew}>
        <Icon name="plus" size={20} />
        {t("prp_add_short")}
      </button>
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

export function NewProperty({ onBack, onCreated }: { onBack: () => void; onCreated: (p: Property) => void }) {
  const [step, setStep] = useState<"kind" | "address" | "details" | "confirm">("kind");
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<AddressCandidate[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));

  async function search() {
    setBusy(true);
    setError(null);
    try {
      setFound(await api.searchAddress(query, city));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  function pick(a: AddressCandidate) {
    set({ address: a, manual: false, street: a.street, house: a.house_number });
    setStep("details");
  }

  function body(): PropertyCreate {
    const a = draft.address;
    return {
      kind: draft.kind,
      address: draft.manual || !a
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
    try {
      onCreated(await api.createProperty(body()));
    } catch (e) {
      setError(propertyError(e));
      setBusy(false);
    }
  }

  const back = () => {
    setError(null);
    if (step === "kind") onBack();
    else if (step === "address") setStep("kind");
    else if (step === "details") setStep("address");
    else setStep("details");
  };

  return (
    <Screen onBack={back}>
      <h1>{t("prp_new_title")}</h1>

      {step === "kind" && (
        <>
          <MascotTip>
            <p>{t("prp_kind_question")}</p>
          </MascotTip>
          <div className="choices">
            {(["apartment", "house"] as const).map((k) => (
              <button
                key={k}
                className="choice-big"
                onClick={() => {
                  set({ kind: k });
                  setStep("address");
                }}
              >
                <span className="row-icon">
                  <Icon name={k === "apartment" ? "building" : "home"} />
                </span>
                {t(k === "apartment" ? "prp_kind_apartment_plain" : "prp_kind_house_plain")}
              </button>
            ))}
          </div>
        </>
      )}

      {step === "address" && !draft.manual && (
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
          <label className="field">
            {t("prp_street_label")}
            <input
              className="input"
              value={query}
              maxLength={120}
              placeholder={t("prp_street_placeholder")}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && query.trim().length >= 3 && void search()}
            />
          </label>
          <button className="button" onClick={search} disabled={busy || query.trim().length < 3}>
            {busy ? t("prp_searching") : t("prp_search")}
          </button>
          {found && found.length > 0 && <p className="small">{t("prp_found_question")}</p>}
          {found?.map((a) => (
            <button key={`${a.street}|${a.house_number}|${a.place_id ?? ""}`} className="row" onClick={() => pick(a)}>
              <span className="row-title">{formatAddress(a)}</span>
              {a.district && <span className="row-sub">{a.district}</span>}
            </button>
          ))}
          {found && found.length === 0 && <p className="hint">{t("prp_not_found")}</p>}
          {found && (
            <button className="link" onClick={() => set({ manual: true })}>
              {t("prp_manual")}
            </button>
          )}
        </section>
      )}

      {step === "address" && draft.manual && (
        <section className="card">
          <p className="hint small">{t("prp_manual_hint")}</p>
          <label className="field">
            {t("prp_street")}
            <input className="input" value={draft.street} maxLength={100} onChange={(e) => set({ street: e.target.value })} />
          </label>
          <label className="field">
            {t("prp_house")}
            <input className="input" value={draft.house} maxLength={20} onChange={(e) => set({ house: e.target.value })} />
          </label>
          <button
            className="button"
            disabled={draft.street.trim().length < 2 || !draft.house.trim()}
            onClick={() => {
              set({ address: null });
              setStep("details");
            }}
          >
            {t("next")}
          </button>
        </section>
      )}

      {step === "details" && (
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
          <div className="chips">
            {[0, 1, 2, 3, 4, 5].map((n) => (
              <button key={n} className={`chip${draft.rooms === n ? " active" : ""}`} onClick={() => set({ rooms: n })}>
                {n === 0 ? t("rooms_studio") : n === 5 ? "5+" : n}
              </button>
            ))}
          </div>
          <label className="field">
            {t("prp_area")} <span className="hint">({t("prp_optional")})</span>
            <input className="input" inputMode="decimal" value={draft.area} maxLength={7} onChange={(e) => set({ area: e.target.value })} />
          </label>
          <button
            className="button"
            disabled={draft.kind === "apartment" && !draft.apartment.trim()}
            onClick={() => setStep("confirm")}
          >
            {t("next")}
          </button>
        </section>
      )}

      {step === "confirm" && (
        <section className="card">
          <h2>{t("prp_confirm_title")}</h2>
          <p className="row-title">
            {draft.kind === "house" ? "🏡" : "🏢"}{" "}
            {formatAddress({
              street: draft.street,
              house_number: draft.house,
              apartment: draft.kind === "apartment" ? draft.apartment : null,
            })}
          </p>
          <p className="hint">
            {roomsText(draft.rooms)}
            {draft.area && ` · ${t("sqm", { area: draft.area })}`}
            {draft.entrance && ` · ${t("prp_entrance")} ${draft.entrance}`}
            {draft.floor && ` · ${t("prp_floor")} ${draft.floor}`}
          </p>
          <button className="button" onClick={create} disabled={busy}>
            {busy ? t("saving") : t("prp_create")}
          </button>
          <button className="link" onClick={() => setStep("details")}>
            {t("edit")}
          </button>
        </section>
      )}

      {error && <p className="error">{error}</p>}
    </Screen>
  );
}

// ------------------------------------------------------------------ PRP-CARD

export function PropertyCard({
  id,
  webApp,
  onBack,
  onOwnership,
  onProfile,
  onNewLease,
  onDeleted,
}: {
  id: string;
  webApp: WebApp;
  onBack: () => void;
  onOwnership: () => void;
  onProfile: () => void;
  onNewLease: (prop: Property) => void;
  onDeleted: () => void;
}) {
  const [prop, setProp] = useState<Property | null>(null);
  const [edit, setEdit] = useState(false);
  const [form, setForm] = useState({ entrance: "", floor: "", rooms: 1, area: "" });
  const [roomName, setRoomName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.property(id).then(setProp).catch((e: unknown) => setError(errorText(e)));
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

  const rooms = prop.units.filter((u) => u.kind === "room");
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

  return (
    <Screen onBack={onBack}>
      <h1 className="left">{formatAddress(prop)}</h1>
      <p className="hint left">
        {roomsText(prop.rooms)}
        {prop.area && ` · ${t("sqm", { area: prop.area })}`}
        {prop.district && ` · ${prop.district}`}
      </p>

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
        {prop.ownership_status !== "verified" && <p className="hint small">{t("prp_card_verify_hint")}</p>}
        <button className={prop.ownership_status === "verified" ? "button secondary" : "button"} onClick={onOwnership}>
          {prop.ownership_status === "unverified" ? t("prp_card_verify") : t("prp_card_verify_status")}
        </button>
        {prop.ownership_status === "verified" && (
          <button className="button" onClick={() => onNewLease(prop)}>
            <Icon name="key" size={20} />
            {t("ls_new_from_property")}
          </button>
        )}
      </section>

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

      <section className="card">
        <h2>{t("prp_units_title")}</h2>
        <p className="hint small">{t("prp_units_hint")}</p>
        <p className="small">• {t("prp_unit_entire")}</p>
        {rooms.map((u) => (
          <p key={u.id} className="small unit">
            • {u.name}
            {u.area && ` · ${t("sqm", { area: u.area })}`}
            <button className="link" disabled={busy} onClick={() => run(() => api.removeRoom(id, u.id))}>
              {t("delete")}
            </button>
          </p>
        ))}
        <label className="field">
          {t("prp_room_name")}
          <input
            className="input"
            value={roomName}
            maxLength={60}
            placeholder={t("prp_room_placeholder")}
            onChange={(e) => setRoomName(e.target.value)}
          />
        </label>
        <button
          className="button secondary"
          disabled={busy || !roomName.trim()}
          onClick={() =>
            run(async () => {
              const updated = await api.addRoom(id, { name: roomName });
              setRoomName("");
              return updated;
            })
          }
        >
          {t("prp_add_room")}
        </button>
      </section>

      {error && <p className="error">{error}</p>}
      <button className="link danger" onClick={remove} disabled={busy}>
        {t("prp_delete")}
      </button>
    </Screen>
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
              {t("own_open_diia")}
            </button>
          </section>

          <section className="card">
            <h2>{t("own_files", { count: String(draftDocs.length), max: String(own.max_documents) })}</h2>
            {draftDocs.map((d) => (
              <p key={d.id} className="small unit">
                📄 {d.filename}
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
              {busy ? t("own_uploading") : t("own_add_file")}
            </button>
            <p className="hint small">{t("own_file_limits", { mb: String(own.max_document_mb) })}</p>
            {draftDocs.length > 0 && (
              <button className="button" disabled={busy} onClick={() => run(() => api.submitOwnership(id))}>
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
