// Анкета об'єкта й рівень заповненості (FR-PRP-8). Форма будується з profileSpec.ts.
// Розділи — акордеоном (ProfileSections); окремі екрани лишились для анкети з оголошення й фото з шапки.

import { useEffect, useRef, useState } from "react";

import { api, type Photo, type Property, type PropertyDetails } from "../api/client";
import { Icon } from "../icons";
import { t, type TextKey } from "../i18n";
import { PROFILE_SECTIONS, type FieldSpec } from "../profileSpec";
import { PHOTO_MAX, PhotoManager } from "./Photos";
import type { WebApp } from "../telegram";
import { Chip, errorCode, errorField, errorText, Fold, formatAddress, Ring, Screen } from "../ui";

type Values = Record<string, unknown>;

const DESCRIPTION_MIN = 80; // як domain/property_profile.py

function label(key: string): string {
  return t(key as TextKey);
}

export function CompletenessBar({ p }: { p: Property }) {
  const c = p.completeness;
  if (!c) return null;
  return (
    <div className="completeness">
      <Ring percent={c.percent} />
      <div className="completeness-text">
        <strong>{label(`pf_level_${c.level}`)}</strong>
        <span className="hint small">{t("pf_answered", { n: String(c.answered), total: String(c.total) })}</span>
      </div>
    </div>
  );
}

export function PublishHint({ p }: { p: Property }) {
  const c = p.completeness;
  if (!c || c.publishable) return null;
  return (
    <div>
      {!c.publishable && (
        <p className="hint small">{t("pf_publish_min", { min: String(c.publish_min), left: String(c.publish_min - c.percent) })}</p>
      )}
    </div>
  );
}

function Field({
  spec,
  value,
  onChange,
  important = false,
}: {
  spec: FieldSpec;
  value: unknown;
  onChange: (v: unknown) => void;
  /** Важливе поле без відповіді — підсвічене, щоб було видно, чого бракує для довіри. */
  important?: boolean;
}) {
  const field = <FieldInput spec={spec} value={value} onChange={onChange} />;
  if (!important) return field;
  return (
    <div className="field-important">
      <span className="need-tag">{t("pf_important_tag")}</span>
      {field}
    </div>
  );
}

function FieldInput({ spec, value, onChange }: { spec: FieldSpec; value: unknown; onChange: (v: unknown) => void }) {
  const name = label(`pf_${spec.key}`);
  switch (spec.kind) {
    case "choice":
      if (spec.options.length <= 6) {
        return (
          <div className="field">
            {name}
            <div className="chips">
              {spec.options.map((o) => (
                <button
                  key={o}
                  type="button"
                  className={`chip${value === o ? " active" : ""}`}
                  aria-pressed={value === o}
                  onClick={() => onChange(value === o ? null : o)}
                >
                  {label(`pf_${spec.key}__${o}`)}
                </button>
              ))}
            </div>
          </div>
        );
      }
      return (
        <label className="field">
          {name}
          <select className="input" value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value || null)}>
            <option value="">— {t("pf_unset")} —</option>
            {spec.options.map((o) => (
              <option key={o} value={o}>
                {label(`pf_${spec.key}__${o}`)}
              </option>
            ))}
          </select>
        </label>
      );
    case "bool":
      return (
        <div className="field">
          {name}
          <div className="chips">
            {([true, false] as const).map((b) => (
              <button
                key={String(b)}
                type="button"
                className={`chip${value === b ? " active" : ""}`}
                aria-pressed={value === b}
                onClick={() => onChange(value === b ? null : b)}
              >
                {b ? t("pf_yes") : t("pf_no")}
              </button>
            ))}
          </div>
        </div>
      );
    case "int":
    case "decimal":
      return (
        <label className="field">
          {name}
          <input
            className="input"
            inputMode={spec.kind === "int" ? "numeric" : "decimal"}
            value={value === null || value === undefined ? "" : String(value)}
            onChange={(e) => {
              const raw = e.target.value.trim().replace(",", ".");
              if (!raw) return onChange(null);
              const n = Number(raw);
              if (Number.isFinite(n)) onChange(spec.kind === "int" ? Math.trunc(n) : raw);
            }}
          />
        </label>
      );
    case "text":
      return (
        <label className="field">
          {name}
          <textarea
            className="input"
            rows={5}
            maxLength={spec.max}
            value={(value as string) ?? ""}
            onChange={(e) => onChange(e.target.value || null)}
          />
          <span className="hint small">{t("pf_desc_hint", { min: String(DESCRIPTION_MIN) })}</span>
        </label>
      );
    case "multi": {
      const selected = (value as string[] | null | undefined) ?? null;
      const toggle = (o: string) => {
        const base = selected ?? [];
        onChange(base.includes(o) ? base.filter((x) => x !== o) : [...base, o]);
      };
      return (
        <div className="field">
          {name}
          <div className="chips">
            {spec.options.map((o) => {
              const on = !!selected?.includes(o);
              return (
                <button key={o} type="button" className={`chip${on ? " active" : ""}`} aria-pressed={on} onClick={() => toggle(o)}>
                  {on && <Icon name="check" size={16} />}
                  {label(`pf_${spec.key}__${o}`)}
                </button>
              );
            })}
          </div>
          {selected === null ? (
            <button type="button" className="link small" onClick={() => onChange([])}>
              {t("pf_none_of_these")}
            </button>
          ) : (
            selected.length === 0 && <span className="hint small">{t("pf_none_selected")}</span>
          )}
        </div>
      );
    }
  }
}

/** Чи є відповідь на поле (порожній список «нічого з цього» — теж відповідь). */
function answered(v: unknown): boolean {
  return v !== null && v !== undefined && v !== "";
}

function sameValue(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

function saveError(e: unknown): { text: string; section?: string } {
  const field = errorField(e);
  const text =
    errorCode(e) === "contacts_in_text"
      ? t("pf_contacts_in_text")
      : field
        ? t("prp_bad_field", { field: label(`pf_${field}`) })
        : errorText(e);
  const where = field ? PROFILE_SECTIONS.find((s) => s.fields.some((f) => f.key === field)) : undefined;
  return { text, section: where?.key };
}

function bodyOf(values: Values): PropertyDetails {
  return Object.fromEntries(Object.entries(values).filter(([, v]) => v !== null && v !== undefined)) as PropertyDetails;
}

/** Анкета акордеоном (ADR 0023): фото й розділи згорнуті, «Заповнено x з n» — лише всередині
 *  розділу. Одна кнопка «Зберегти» на всю анкету з'являється внизу, щойно є зміни; вихід зі
 *  сторінки чи вкладки зберігає незбережене сам. */
export function ProfileSections({
  p,
  onSaved,
  photos,
  onPhotos,
  webApp,
}: {
  p: Property;
  onSaved: (p: Property) => void;
  photos: Photo[] | null;
  onPhotos: (photos: Photo[]) => void;
  webApp: WebApp | null;
}) {
  const [values, setValues] = useState<Values>(() => ({ ...(p.details ?? {}) }));
  const [opened, setOpened] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const saved = (p.details ?? {}) as Values;
  const dirty = PROFILE_SECTIONS.some((s) => s.fields.some((f) => !sameValue(values[f.key], saved[f.key])));
  const missing = p.completeness?.missing_important ?? [];

  // Автозбереження при виході: останні значення — в ref, бо прибирання ефекту бачить лише перший рендер.
  const latest = useRef({ values, dirty, id: p.id, onSaved });
  latest.current = { values, dirty, id: p.id, onSaved };
  useEffect(
    () => () => {
      const last = latest.current;
      if (last.dirty) api.updateProfile(last.id, bodyOf(last.values)).then(last.onSaved, () => undefined);
    },
    [],
  );

  async function save() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const updated = await api.updateProfile(p.id, bodyOf(values));
      onSaved(updated);
      setValues({ ...(updated.details ?? {}) });
      setMessage(t("pf_saved"));
    } catch (e) {
      const { text, section } = saveError(e);
      setError(text);
      // Помилка в полі — відкриваємо той розділ.
      if (section) setOpened(section);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section className="card">
        <h2>{t("pf_title")}</h2>
        <CompletenessBar p={p} />
        <PublishHint p={p} />
      </section>
      <Fold
        id={`pf:${p.id}:photos`}
        title={t("ph_title")}
        aside={photos !== null && photos.length === 0 ? <Chip tone="warn">{t("ph_none")}</Chip> : undefined}
        className="pf-section"
      >
        <p className="hint small">{t("ph_count", { n: String(photos?.length ?? 0), max: String(PHOTO_MAX) })}</p>
        {photos && <PhotoManager propertyId={p.id} photos={photos} onChange={onPhotos} webApp={webApp} />}
      </Fold>
      {PROFILE_SECTIONS.map((s) => {
        const done = s.fields.filter((f) => answered(values[f.key])).length;
        return (
          <Fold
            key={s.key}
            id={`pf:${p.id}:${s.key}`}
            title={label(`pf_section_${s.key}`)}
            aside={<Ring percent={Math.round((100 * done) / s.fields.length)} size={34} />}
            open={opened === s.key ? true : undefined}
            onToggle={(o) => {
              if (!o && opened === s.key) setOpened(null);
            }}
            className="pf-section"
          >
            <p className="hint small">{t("pf_section_progress", { n: String(done), total: String(s.fields.length) })}</p>
            {s.fields.map((f) => (
              <Field
                key={f.key}
                spec={f}
                value={values[f.key]}
                important={missing.includes(f.key) && !answered(values[f.key])}
                onChange={(v) => {
                  setMessage(null);
                  setValues((x) => ({ ...x, [f.key]: v }));
                }}
              />
            ))}
          </Fold>
        );
      })}
      {(dirty || error || message) && (
        <div className="sticky-actions">
          {error && <p className="error">{error}</p>}
          {message && !dirty && <p className="ok">{message}</p>}
          {dirty && (
            <button className="button" onClick={save} disabled={busy}>
              {busy ? t("saving") : t("pf_save")}
            </button>
          )}
        </div>
      )}
    </>
  );
}

/** Окремий екран анкети (з оголошення) або одного розділу (з шапки квартири — «Додати фото»). */
export function PropertyProfile({
  id,
  webApp,
  onBack,
  section: only,
}: {
  id: string;
  webApp: WebApp | null;
  onBack: () => void;
  /** Відкрити одразу цей розділ; «Назад» і «Зберегти» повертають туди, звідки прийшли. */
  section?: string;
}) {
  const [prop, setProp] = useState<Property | null>(null);
  const [photos, setPhotos] = useState<Photo[] | null>(null);
  const [values, setValues] = useState<Values>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .property(id)
      .then((p) => {
        setProp(p);
        setValues({ ...(p.details ?? {}) });
      })
      .catch((e: unknown) => setError(errorText(e)));
    api
      .photos(id)
      .then(setPhotos)
      .catch(() => setPhotos([]));
  }, [id]);

  if (!prop) {
    return (
      <Screen onBack={onBack}>
        {error ? <p className="error">{error}</p> : <p className="hint">{t("loading")}</p>}
      </Screen>
    );
  }

  if (only === "photos") {
    return (
      <Screen onBack={onBack} title={t("pf_title")}>
        <h2>{t("ph_title")}</h2>
        {photos && <PhotoManager propertyId={id} photos={photos} onChange={setPhotos} webApp={webApp} />}
      </Screen>
    );
  }

  const section = only ? PROFILE_SECTIONS.find((s) => s.key === only) : undefined;
  if (section) {
    const done = section.fields.filter((f) => answered(values[f.key])).length;
    const missing = prop.completeness?.missing_important ?? [];
    const save = async () => {
      setBusy(true);
      setError(null);
      try {
        await api.updateProfile(id, bodyOf(values));
        onBack();
      } catch (e) {
        setError(saveError(e).text);
      } finally {
        setBusy(false);
      }
    };
    return (
      <Screen onBack={onBack} title={t("pf_title")}>
        <h2>{label(`pf_section_${section.key}`)}</h2>
        <p className="hint small">{t("pf_section_progress", { n: String(done), total: String(section.fields.length) })}</p>
        <section className="card">
          {section.fields.map((f) => (
            <Field
              key={f.key}
              spec={f}
              value={values[f.key]}
              important={missing.includes(f.key) && !answered(values[f.key])}
              onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))}
            />
          ))}
        </section>
        <div className="sticky-actions">
          {error && <p className="error">{error}</p>}
          <button className="button" onClick={save} disabled={busy}>
            {busy ? t("saving") : t("pf_save")}
          </button>
        </div>
      </Screen>
    );
  }

  return (
    <Screen onBack={onBack} title={t("pf_title")}>
      <p className="hint">{formatAddress(prop)}</p>
      <p className="hint small">{t("pf_intro")}</p>
      <ProfileSections p={prop} onSaved={setProp} photos={photos} onPhotos={setPhotos} webApp={webApp} />
    </Screen>
  );
}
