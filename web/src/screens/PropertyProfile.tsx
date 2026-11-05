// Анкета об'єкта й рівень заповненості (FR-PRP-8). Форма будується з profileSpec.ts.
// Перша версія: функціональна, дизайн і тексти ще доопрацюємо.

import { useEffect, useState } from "react";

import { api, type Property, type PropertyDetails } from "../api/client";
import { t, type TextKey } from "../i18n";
import { PROFILE_SECTIONS, type FieldSpec } from "../profileSpec";
import { errorField, errorText, formatAddress, Screen } from "../ui";

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
      <div className="bar">
        <span className={`fill ${c.level}`} style={{ width: `${c.percent}%` }} />
      </div>
      <p className="small">
        {t("pf_completeness", { percent: String(c.percent) })} · {label(`pf_level_${c.level}`)}
      </p>
    </div>
  );
}

function Field({ spec, value, onChange }: { spec: FieldSpec; value: unknown; onChange: (v: unknown) => void }) {
  const name = label(`pf_${spec.key}`);
  switch (spec.kind) {
    case "choice":
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
        <label className="field">
          {name}
          <select
            className="input"
            value={value === true ? "yes" : value === false ? "no" : ""}
            onChange={(e) => onChange(e.target.value === "" ? null : e.target.value === "yes")}
          >
            <option value="">— {t("pf_unset")} —</option>
            <option value="yes">{t("pf_yes")}</option>
            <option value="no">{t("pf_no")}</option>
          </select>
        </label>
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
      const toggle = (o: string, on: boolean) => {
        const base = selected ?? [];
        onChange(on ? [...base, o] : base.filter((x) => x !== o));
      };
      return (
        <fieldset className="field multi">
          <legend>{name}</legend>
          {spec.options.map((o) => (
            <label key={o} className="check">
              <input type="checkbox" checked={!!selected?.includes(o)} onChange={(e) => toggle(o, e.target.checked)} />
              {label(`pf_${spec.key}__${o}`)}
            </label>
          ))}
          {selected === null && (
            <button className="link small" onClick={() => onChange([])}>
              {t("pf_none_of_these")}
            </button>
          )}
        </fieldset>
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
      setError(field ? t("prp_bad_field", { field: label(`pf_${field}`) }) : errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const missing = prop.completeness?.missing_important ?? [];

  return (
    <Screen onBack={onBack}>
      <h1>{t("pf_title")}</h1>
      <p className="hint">{formatAddress(prop)}</p>
      <section className="card">
        <CompletenessBar p={prop} />
        <p className="hint small">{t("pf_intro")}</p>
        {missing.length > 0 && (
          <>
            <p className="small">{t("pf_missing")}</p>
            <p className="small">{missing.map((k) => label(`pf_${k}`)).join(" · ")}</p>
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

      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}
      <button className="button" onClick={save} disabled={busy}>
        {busy ? t("saving") : t("pf_save")}
      </button>
    </Screen>
  );
}
