// Лічильники й показники (M5, BOT_FLOWS §11, FR-UTL-1..4). Домовик не передає показники
// постачальнику сам і не заходить у кабінети (правило 8): зберігає історію, нагадує й дає посилання.

import { useCallback, useEffect, useState } from "react";

import {
  api,
  ApiFailure,
  type Meter,
  type MeterIn,
  type MeterKind,
  type MetersOverview,
  type Provider,
  type Reading,
  type ReadingResult,
} from "../api/client";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import { confirm, openLink, type WebApp } from "../telegram";
import { Chip, errorText, formatDate, MascotTip, Screen, Sheet } from "../ui";
import { Chips } from "./Leases";

const k = (key: string) => t(key as TextKey);
const KINDS: MeterKind[] = ["electricity", "water_cold", "water_hot", "gas", "heat"];
const EMOJI: Record<MeterKind, string> = {
  electricity: "⚡",
  water_cold: "💧",
  water_hot: "♨️",
  gas: "🔥",
  heat: "🌡",
};

function num(v: string | number): string {
  return Number(v).toLocaleString("uk-UA", { maximumFractionDigits: 3 });
}

function money(v: string | number): string {
  return `${Number(v).toLocaleString("uk-UA", { maximumFractionDigits: 2 })} ₴`;
}

function dayMonth(iso: string): string {
  return `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
}

function meterName(m: Pick<Meter, "kind" | "label">): string {
  const kind = k(`mt_kind_${m.kind}`);
  return m.label ? `${kind} · ${m.label}` : kind;
}

function readingError(e: unknown): string {
  if (e instanceof ApiFailure && e.field === "value") return t("mt_err_value");
  if (e instanceof ApiFailure && e.field === "photo") return t("mt_err_photo");
  return errorText(e);
}

async function readImage(file: File): Promise<Blob> {
  const data = await file.arrayBuffer();
  return new Blob([data], { type: file.type || "image/jpeg" });
}

// ---------------------------------------------------------------- подача показника

function ReadingSheet({
  meter,
  webApp,
  onClose,
  onSaved,
}: {
  meter: Meter;
  webApp: WebApp;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [photo, setPhoto] = useState<Blob | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ReadingResult | null>(null);
  const unit = k(`mt_unit_${meter.kind}`);
  const previous = meter.last?.value;

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const blob = await readImage(file);
      setPhoto(blob);
      setPreview(URL.createObjectURL(blob));
    } catch {
      setError(t("mt_err_photo"));
    }
  }

  async function save() {
    const clean = value.trim().replace(/\s/g, "").replace(",", ".");
    if (!/^\d+(\.\d{1,3})?$/.test(clean)) return setError(t("mt_err_value"));
    if (previous !== undefined && Number(clean) < Number(previous)) {
      return setError(t("mt_err_previous", { value: num(previous) }));
    }
    setBusy(true);
    setError(null);
    try {
      setResult(await api.submitReading(meter.id, clean, photo ?? undefined));
      onSaved();
    } catch (e) {
      if (e instanceof ApiFailure && e.field === "value" && previous !== undefined) {
        setError(t("mt_err_previous", { value: num(previous) }));
      } else setError(readingError(e));
    } finally {
      setBusy(false);
    }
  }

  async function markSent(close: () => void) {
    if (!result) return;
    setBusy(true);
    try {
      await api.readingSent(result.reading.id);
      onSaved();
      close();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet label={t("mt_sheet_title", { kind: meterName(meter) })} onClose={onClose}>
      {(close) => (
        <>
          <div className="sheet-head">
            <h2>{t("mt_sheet_title", { kind: meterName(meter) })}</h2>
            <button className="icon-button" aria-label={t("ab_close")} onClick={close}>
              <Icon name="close" size={20} />
            </button>
          </div>
          {result ? (
            <section className="card">
              <p>
                <b>{t("mt_saved", { value: num(result.reading.value), unit })}</b>
              </p>
              {result.reading.consumed !== null && result.reading.consumed !== undefined && (
                <p className="hint">
                  {t("mt_saved_consumed", { value: num(result.reading.consumed), unit })}
                  {result.reading.estimate ? ` · ${t("mt_estimate", { sum: money(result.reading.estimate) })}` : ""}
                </p>
              )}
              <p className="hint small">{t("mt_pass_on")}</p>
              {result.cabinet_url && result.meter.provider && (
                <button className="button secondary" onClick={() => openLink(webApp, result.cabinet_url ?? "")}>
                  <Icon name="link" size={20} />
                  {t("mt_open_cabinet", { name: result.meter.provider.name })}
                </button>
              )}
              <button className="button" disabled={busy} onClick={() => markSent(close)}>
                <Icon name="check" size={20} />
                {t("mt_mark_sent")}
              </button>
              <button className="button ghost" onClick={close}>
                {t("mt_done")}
              </button>
            </section>
          ) : (
            <section className="card">
              <p className="hint small">{t("mt_photo_step")}</p>
              {preview && <img className="meter-preview" src={preview} alt={t("mt_photo")} />}
              <label className={`button ${photo ? "ghost" : "secondary"} file-button${busy ? " disabled" : ""}`}>
                <Icon name="photo" size={20} />
                {photo ? t("mt_retake_photo") : t("mt_take_photo")}
                <input type="file" accept="image/*" capture="environment" hidden onChange={onFile} />
              </label>
              <label className="field">
                {t("mt_value_label")}, {k(`mt_unit_${meter.kind}`)}
                <input
                  className="input meter-input"
                  inputMode="decimal"
                  autoComplete="off"
                  placeholder={previous !== undefined ? num(previous) : "0"}
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                />
              </label>
              {previous !== undefined && !error && (
                <p className="hint small">{t("mt_value_hint", { value: num(previous) })}</p>
              )}
              {error && <p className="error">{error}</p>}
              <button className="button" disabled={busy || !value.trim()} onClick={save}>
                {t("mt_save")}
              </button>
            </section>
          )}
          {result && error && <p className="error">{error}</p>}
        </>
      )}
    </Sheet>
  );
}

// ---------------------------------------------------------------- власник: лічильник

function MeterSheet({
  meter,
  providers,
  webApp,
  onClose,
  onSaved,
  save,
  remove,
}: {
  meter: Meter | null;
  providers: Provider[];
  webApp: WebApp;
  onClose: () => void;
  onSaved: (o: MetersOverview) => void;
  save: (body: MeterIn) => Promise<MetersOverview>;
  remove: (() => Promise<MetersOverview>) | null;
}) {
  const [kind, setKind] = useState<MeterKind>(meter?.kind ?? "electricity");
  const [providerId, setProviderId] = useState<number | null>(meter?.provider?.id ?? null);
  const [account, setAccount] = useState(meter?.account_number ?? "");
  const [label, setLabel] = useState(meter?.label ?? "");
  const [submits, setSubmits] = useState<MeterIn["submits"]>(meter?.submits ?? "tenant");
  const [pays, setPays] = useState<MeterIn["pays"]>(meter?.pays ?? "tenant");
  const [tariff, setTariff] = useState(meter?.tariff ? String(Number(meter.tariff)) : "");
  const [initial, setInitial] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const forKind = providers.filter((p) => p.meter_kinds.includes(kind));
  // Змінили тип — підставляємо єдиного постачальника міста для нього.
  useEffect(() => {
    setProviderId((current) => {
      if (current !== null && forKind.some((p) => p.id === current)) return current;
      return forKind.length === 1 ? (forKind[0]?.id ?? null) : null;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind]);

  async function run(action: () => Promise<MetersOverview>, close: () => void) {
    setBusy(true);
    setError(null);
    try {
      onSaved(await action());
      close();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const decimal = (v: string) => v.trim().replace(",", ".");
  const body = (): MeterIn => ({
    kind,
    provider_id: providerId,
    account_number: account.trim() || null,
    label: label.trim() || null,
    submits,
    pays,
    tariff: tariff.trim() ? decimal(tariff) : null,
    initial_value: !meter && initial.trim() ? decimal(initial) : null,
  });

  const title = meter ? t("mt_form_edit") : t("mt_form_add");
  return (
    <Sheet label={title} onClose={onClose}>
      {(close) => (
        <>
          <div className="sheet-head">
            <h2>{title}</h2>
            <button className="icon-button" aria-label={t("ab_close")} onClick={close}>
              <Icon name="close" size={20} />
            </button>
          </div>
          <section className="card">
            <div className="field">
              {t("mt_form_kind")}
              <Chips<MeterKind> value={kind} options={KINDS} label={(v) => `${EMOJI[v]} ${k(`mt_kind_${v}`)}`} onChange={setKind} />
            </div>
            <div className="field">
              {t("mt_form_provider")}
              <Chips<number>
                value={providerId ?? 0}
                options={[...forKind.map((p) => p.id), 0]}
                label={(id) => (id === 0 ? t("mt_form_no_provider") : (providers.find((p) => p.id === id)?.name ?? ""))}
                onChange={(id) => setProviderId(id === 0 ? null : id)}
              />
              {providerId === null && <span className="hint small">{t("mt_form_no_provider_hint")}</span>}
            </div>
            <label className="field">
              {t("mt_form_account")}
              <input className="input" maxLength={40} value={account} onChange={(e) => setAccount(e.target.value)} />
            </label>
            <label className="field">
              {t("mt_form_label")}
              <input className="input" maxLength={40} placeholder={t("mt_form_label_ph")} value={label} onChange={(e) => setLabel(e.target.value)} />
            </label>
            <div className="field">
              {t("mt_form_submits")}
              <Chips<MeterIn["submits"] & string>
                value={submits ?? "tenant"}
                options={["tenant", "landlord"]}
                label={(v) => k(`mt_role_${v}`)}
                onChange={setSubmits}
              />
            </div>
            <div className="field">
              {t("mt_form_pays")}
              <Chips<MeterIn["pays"] & string>
                value={pays ?? "tenant"}
                options={["tenant", "landlord"]}
                label={(v) => k(`mt_role_${v}`)}
                onChange={setPays}
              />
            </div>
            <label className="field">
              {t("mt_form_tariff")}
              <input className="input" inputMode="decimal" value={tariff} onChange={(e) => setTariff(e.target.value)} />
              <span className="hint small">{t("mt_form_tariff_hint")}</span>
            </label>
            {!meter && (
              <label className="field">
                {t("mt_form_initial")}, {k(`mt_unit_${kind}`)}
                <input className="input" inputMode="decimal" value={initial} onChange={(e) => setInitial(e.target.value)} />
                <span className="hint small">{t("mt_form_initial_hint")}</span>
              </label>
            )}
            <button className="button" disabled={busy} onClick={() => run(() => save(body()), close)}>
              {t("mt_form_save")}
            </button>
            {remove && (
              <button
                className="link danger"
                disabled={busy}
                onClick={async () => {
                  if (!(await confirm(webApp, t("mt_form_remove_confirm")))) return;
                  await run(remove, close);
                }}
              >
                {t("mt_form_remove")}
              </button>
            )}
          </section>
          {error && <p className="error">{error}</p>}
        </>
      )}
    </Sheet>
  );
}

// ---------------------------------------------------------------- фото показника

function PhotoSheet({ reading, onClose }: { reading: Reading; onClose: () => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let objectUrl: string | null = null;
    api
      .readingPhoto(reading.id)
      .then((r) => {
        objectUrl = URL.createObjectURL(new Blob([r.data], { type: r.mime }));
        setUrl(objectUrl);
      })
      .catch((e: unknown) => setError(errorText(e)));
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [reading.id]);
  return (
    <Sheet label={t("mt_photo")} onClose={onClose}>
      {(close) => (
        <>
          <div className="sheet-head">
            <h2>
              {num(reading.value)} · {formatDate(reading.reading_date)}
            </h2>
            <button className="icon-button" aria-label={t("ab_close")} onClick={close}>
              <Icon name="close" size={20} />
            </button>
          </div>
          {!url && !error && <div className="skeleton pdf-skeleton" />}
          {url && <img className="receipt-img" src={url} alt={t("mt_photo")} />}
          {error && <p className="error">{error}</p>}
        </>
      )}
    </Sheet>
  );
}

// ---------------------------------------------------------------- картка лічильника

function MeterCard({
  m,
  o,
  webApp,
  onSubmit,
  onEdit,
  onChanged,
}: {
  m: Meter;
  o: MetersOverview;
  webApp: WebApp;
  onSubmit: () => void;
  onEdit: () => void;
  onChanged: (o: MetersOverview) => void;
}) {
  const [photo, setPhoto] = useState<Reading | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const unit = k(`mt_unit_${m.kind}`);
  const last = m.last;
  const w = m.window;

  return (
    <section className={`card meter-card${m.is_active ? "" : " off"}`}>
      <div className="meter-head">
        <span className="meter-emoji" aria-hidden="true">
          {EMOJI[m.kind]}
        </span>
        <div className="meter-title">
          <b>{meterName(m)}</b>
          {m.provider && <span className="hint small">{m.provider.name}</span>}
        </div>
        {!m.is_active ? (
          <Chip>{t("mt_off")}</Chip>
        ) : m.due ? (
          <Chip tone={m.i_submit ? "warn" : "info"}>{t("mt_due")}</Chip>
        ) : null}
      </div>

      {m.is_active && (
        <p className="hint small">
          {w
            ? w.open
              ? t("mt_window_open", { to: dayMonth(w.end) })
              : t("mt_window", { from: dayMonth(w.start), to: dayMonth(w.end) })
            : t("mt_no_window")}
          {" · "}
          {k(`mt_submits_${m.submits}`)}
        </p>
      )}
      {m.account_number && <p className="hint small">{t("mt_account", { n: m.account_number })}</p>}

      {last ? (
        <div className="meter-last">
          <span className="meter-value">
            {num(last.value)} <small>{unit}</small>
          </span>
          <span className="hint small">
            {formatDate(last.reading_date)}
            {last.consumed ? ` · ${t("mt_consumed", { value: num(last.consumed), unit })}` : ""}
            {last.estimate ? ` · ${t("mt_estimate", { sum: money(last.estimate) })}` : ""}
          </span>
          {last.mine && m.i_submit && (
            <span className={`small ${last.sent_to_provider_at ? "ok-text" : "hint"}`}>
              {last.sent_to_provider_at ? `✓ ${t("mt_sent")}` : t("mt_not_sent")}
            </span>
          )}
        </div>
      ) : (
        <p className="hint">{t("mt_none_yet")}</p>
      )}

      <div className="meter-actions">
        {m.is_active && o.can_submit && (
          <button className={m.due && m.i_submit ? "button" : "button secondary"} onClick={onSubmit}>
            <Icon name="photo" size={20} />
            {t("mt_submit")}
          </button>
        )}
        {last?.mine && m.i_submit && !last.sent_to_provider_at && m.provider?.cabinet_url && (
          <button className="button ghost" onClick={() => openLink(webApp, m.provider?.cabinet_url ?? "")}>
            <Icon name="link" size={20} />
            {t("mt_open_cabinet", { name: m.provider.name })}
          </button>
        )}
        {o.can_manage && (
          <button className="link" onClick={onEdit}>
            {t("mt_edit")}
          </button>
        )}
      </div>

      {m.readings.length > 0 && (
        <details className="meter-history">
          <summary>{t("mt_history")}</summary>
          {m.readings.map((r) => (
            <div key={r.id} className="meter-row">
              <span>{formatDate(r.reading_date)}</span>
              <span className="meter-row-value">
                {num(r.value)}
                {r.consumed ? <small> +{num(r.consumed)}</small> : null}
              </span>
              <span className="hint small">{r.submitted_by_name}</span>
              <span className="meter-row-actions">
                {r.has_photo && (
                  <button className="link" onClick={() => setPhoto(r)}>
                    {t("mt_photo")}
                  </button>
                )}
                {r.can_delete && (
                  <button
                    className="link danger"
                    disabled={busy}
                    onClick={async () => {
                      if (!(await confirm(webApp, t("mt_delete_reading_confirm", { value: num(r.value) })))) return;
                      setBusy(true);
                      setError(null);
                      try {
                        onChanged(await api.deleteReading(r.id));
                      } catch (e) {
                        setError(errorText(e));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    {t("mt_delete_reading")}
                  </button>
                )}
              </span>
            </div>
          ))}
        </details>
      )}
      {error && <p className="error">{error}</p>}
      {photo && <PhotoSheet reading={photo} onClose={() => setPhoto(null)} />}
    </section>
  );
}

// ---------------------------------------------------------------- екран

export function MetersScreen({
  leaseId,
  propertyId,
  webApp,
  onBack,
}: {
  leaseId?: string;
  propertyId?: string;
  webApp: WebApp;
  onBack: () => void;
}) {
  const [o, setO] = useState<MetersOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<Meter | null>(null);
  const [editing, setEditing] = useState<Meter | "new" | null>(null);

  const load = useCallback(() => {
    const req = leaseId ? api.leaseMeters(leaseId) : api.propertyMeters(propertyId ?? "");
    req.then(setO).catch((e: unknown) => setError(errorText(e)));
  }, [leaseId, propertyId]);
  useEffect(load, [load]);

  if (!o) {
    return (
      <Screen onBack={onBack} title={t("mt_title")}>
        {error ? <p className="error">{error}</p> : <div className="skeleton" style={{ height: 200 }} />}
      </Screen>
    );
  }

  const active = o.meters.filter((m) => m.is_active);
  const off = o.meters.filter((m) => !m.is_active);
  return (
    <Screen onBack={onBack} title={t("mt_title")}>
      {o.meters.length === 0 && (
        <MascotTip>
          <p>{o.can_manage ? t("mt_empty_landlord") : t("mt_empty_tenant")}</p>
        </MascotTip>
      )}
      {[...active, ...off].map((m) => (
        <MeterCard
          key={m.id}
          m={m}
          o={o}
          webApp={webApp}
          onSubmit={() => setSubmitting(m)}
          onEdit={() => setEditing(m)}
          onChanged={setO}
        />
      ))}
      {o.can_manage && (
        <button className="button" onClick={() => setEditing("new")}>
          <Icon name="plus" size={20} />
          {t("mt_add")}
        </button>
      )}
      {error && <p className="error">{error}</p>}
      {submitting && (
        <ReadingSheet meter={submitting} webApp={webApp} onClose={() => setSubmitting(null)} onSaved={load} />
      )}
      {editing && (
        <MeterSheet
          meter={editing === "new" ? null : editing}
          providers={o.providers}
          webApp={webApp}
          onClose={() => setEditing(null)}
          onSaved={setO}
          save={(body) => (editing === "new" ? api.addMeter(o.property_id, body) : api.updateMeter(editing.id, body))}
          remove={editing === "new" ? null : () => api.removeMeter(editing.id)}
        />
      )}
    </Screen>
  );
}
