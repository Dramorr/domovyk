// Анкета об'єкта й рівень заповненості (FR-PRP-8). Форма будується з profileSpec.ts.
// Перша версія: функціональна, дизайн і тексти ще доопрацюємо.

import { useEffect, useState } from "react";

import { api, type Property, type PropertyDetails } from "../api/client";
import { t, type TextKey } from "../i18n";
import { PROFILE_SECTIONS, type FieldSpec } from "../profileSpec";
import { Chip, errorCode, errorField, errorText, formatAddress, MascotTip, Ring, Screen } from "../ui";

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

function Field({ spec, value, onChange }: { spec: FieldSpec; value: unknown; onChange: (v: unknown) => void }) {
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
                  {on && "✓ "}
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

export function PropertyProfile({ id, onBack }: { id: string; onBack: () => void }) {
  const [prop, setProp] = useState<Property | null>(null);
  const [values, setValues] = useState<Values>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .property(id)
      .then((p) => {
        setProp(p);
        setValues({ ...(p.details ?? {}) });
      })
      .catch((e: unknown) => setError(errorText(e)));
  }, [id]);

  if (!prop) {
    return (
      <Screen onBack={onBack}>
        {error ? <p className="error">{error}</p> : <p className="hint">{t("loading")}</p>}
      </Screen>
    );
  }

  async function save() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const body = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== null && v !== undefined));
      const updated = await api.updateProfile(id, body as PropertyDetails);
      setProp(updated);
      setValues({ ...(updated.details ?? {}) });
      setMessage(t("pf_saved"));
    } catch (e) {
      const field = errorField(e);
      if (errorCode(e) === "contacts_in_text") setError(t("pf_contacts_in_text"));
      else setError(field ? t("prp_bad_field", { field: label(`pf_${field}`) }) : errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const missing = prop.completeness?.missing_important ?? [];

  return (
    <Screen onBack={onBack} title={t("pf_title")}>
      <p className="hint">{formatAddress(prop)}</p>
      <MascotTip>
        <p>{t("pf_intro")}</p>
      </MascotTip>
      <section className="card">
        <CompletenessBar p={prop} />
        <PublishHint p={prop} />
        {missing.length > 0 && (
          <>
            <p className="small">{t("pf_missing")}</p>
            <div className="chips">
              {missing.map((k) => (
                <Chip key={k} tone="warn">
                  {label(`pf_${k}`)}
                </Chip>
              ))}
            </div>
          </>
        )}
      </section>

      {PROFILE_SECTIONS.map((section) => (
        <section key={section.key} className="card">
          <h2>{label(`pf_section_${section.key}`)}</h2>
          {section.fields.map((f) => (
            <Field key={f.key} spec={f} value={values[f.key]} onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))} />
          ))}
        </section>
      ))}

      <div className="sticky-actions">
        {error && <p className="error">{error}</p>}
        {message && <p className="ok">{message}</p>}
        <button className="button" onClick={save} disabled={busy}>
          {busy ? t("saving") : t("pf_save")}
        </button>
      </div>
    </Screen>
  );
}
