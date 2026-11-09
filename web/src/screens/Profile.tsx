// PROF-1 і PROF-TENANT (BOT_FLOWS §16, FR-USR-3, FR-USR-5).
// Вкладки «Основне · Анкета · Акаунт» під шапкою і одна кнопка «Зберегти» внизу —
// зберігає зміни з усіх вкладок разом; вкладка з незбереженим позначена крапкою.

import { useEffect, useState } from "react";

import { api, type TenantProfile, type User } from "../api/client";
import { t } from "../i18n";
import { confirm, type WebApp } from "../telegram";
import { Icon } from "../icons";
import { Chip, errorCode, errorText, Screen } from "../ui";
import { isValidName } from "./Onboarding";

const HOUSEHOLD_MAX = 20;

type ProfileTab = "main" | "tenant" | "account";

/** Рядок-перемикач: підпис ліворуч, повзунок праворуч. */
function SwitchRow({ label, value, onChange }: { label: string; value: boolean | null | undefined; onChange: (v: boolean) => void }) {
  return (
    <label className="switch-row">
      <span>{label}</span>
      <input type="checkbox" role="switch" checked={value === true} onChange={(e) => onChange(e.target.checked)} />
      <span className="switch" aria-hidden="true" />
    </label>
  );
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

export function Profile({
  user,
  webApp,
  onBack,
  onUserChange,
  onDeleted,
}: {
  user: User;
  webApp: WebApp;
  onBack?: () => void;
  onUserChange: (user: User) => void;
  onDeleted: () => void;
}) {
  const tenant = user.mode !== "landlord";
  const [name, setName] = useState(user.display_name);
  const [profile, setProfile] = useState<TenantProfile>({});
  const [saved, setSaved] = useState<TenantProfile>({});
  const [loaded, setLoaded] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<ProfileTab>("main");

  useEffect(() => {
    if (!tenant) return;
    api
      .tenantProfile()
      .then((p) => {
        setProfile(p);
        setSaved(p);
        setLoaded(true);
      })
      .catch((e: unknown) => setError(errorText(e)));
  }, [tenant]);

  const nameChanged = name.trim() !== user.display_name;
  const profileChanged = tenant && loaded && !same(profile, saved);
  const dirty = nameChanged || profileChanged;

  function patch(update: Partial<TenantProfile>) {
    setProfile((p) => ({ ...p, ...update }));
    setMessage(null);
  }

  async function save() {
    if (nameChanged && !isValidName(name)) {
      setError(t("onb_name_invalid"));
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      if (nameChanged) onUserChange(await api.rename(name.trim()));
      if (profileChanged) {
        const p = await api.saveTenantProfile(profile);
        setProfile(p);
        setSaved(p);
      }
      setMessage(t("prof_saved"));
      window.setTimeout(() => setMessage(null), 2500);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function deleteAccount() {
    if (!(await confirm(webApp, t("prof_delete_confirm")))) return;
    if (!(await confirm(webApp, t("prof_delete_confirm2")))) return;
    setBusy(true);
    try {
      await api.deleteAccount();
      onDeleted();
    } catch (e) {
      setError(errorCode(e) === "has_open_lease" ? t("prof_delete_has_lease") : errorText(e));
      setBusy(false);
    }
  }

  const household = profile.household_size ?? null;
  const tabs: { key: ProfileTab; label: string; dirty: boolean }[] = [
    { key: "main", label: t("prof_group_main"), dirty: nameChanged },
    ...(tenant ? [{ key: "tenant" as const, label: t("prof_tab_tenant"), dirty: !!profileChanged }] : []),
    { key: "account", label: t("prof_group_account"), dirty: false },
  ];
  const initial = (name.trim() || user.display_name).charAt(0).toUpperCase();

  return (
    <Screen onBack={onBack} title={t("prof_title")}>
      <section className="profile-head">
        <span className="avatar" aria-hidden="true">
          {initial}
        </span>
        <div>
          <p className="profile-name">{name.trim() || user.display_name}</p>
          <div className="chips">
            {user.has_phone && (
              <Chip tone="ok">
                <Icon name="check" size={14} /> {t("prof_phone_ok")}
              </Chip>
            )}
            {user.role !== "user" && <Chip tone="warn">{t(`role_${user.role}`)}</Chip>}
          </div>
        </div>
      </section>

      {/* Розділи — «клавіші» збоку: активна висувається й зливається з карткою. */}
      <div className="piano">
        <div className="piano-keys" role="tablist" aria-orientation="vertical" aria-label={t("prof_title")}>
          {tabs.map((x) => (
            <button
              key={x.key}
              type="button"
              role="tab"
              aria-selected={tab === x.key}
              className={`piano-key${tab === x.key ? " active" : ""}`}
              onClick={() => setTab(x.key)}
            >
              <span>{x.label}</span>
              {x.dirty && <span className="tab-dot" aria-label={t("pf_unsaved")} />}
            </button>
          ))}
        </div>
        <div className="piano-panel">
      {tab === "main" && (
      <section className="card group tab-panel" key="main">
        <label className="field">
          {t("prof_name")}
          <input
            className="input"
            value={name}
            maxLength={40}
            autoComplete="name"
            onChange={(e) => {
              setName(e.target.value);
              setMessage(null);
            }}
          />
        </label>
        <p className="hint small">{t("prof_name_hint")}</p>
      </section>
      )}

      {tenant && tab === "tenant" && (
        <>
          <section className="card group tab-panel" key="tenant">
            <p className="hint small group-hint">{t("prof_tenant_hint")}</p>
            {!loaded && !error && <div className="skeleton" />}
            {loaded && (
              <>
                <label className="field">
                  {t("prof_occupation")}
                  <input
                    className="input"
                    maxLength={100}
                    placeholder={t("prof_occupation_placeholder")}
                    value={profile.occupation ?? ""}
                    onChange={(e) => patch({ occupation: e.target.value })}
                  />
                </label>
                <label className="field">
                  {t("prof_about")}
                  <textarea
                    className="input"
                    rows={3}
                    maxLength={1000}
                    placeholder={t("prof_about_placeholder")}
                    value={profile.about ?? ""}
                    onChange={(e) => patch({ about: e.target.value })}
                  />
                </label>
                <div className="stepper-row">
                  <span>{t("prof_household")}</span>
                  <div className="num-stepper">
                    <button
                      type="button"
                      aria-label={t("prof_less")}
                      disabled={household === null || household <= 1}
                      onClick={() => patch({ household_size: Math.max(1, (household ?? 1) - 1) })}
                    >
                      <Icon name="minus" size={18} />
                    </button>
                    <span className="num" aria-live="polite">
                      {household ?? "—"}
                    </span>
                    <button
                      type="button"
                      aria-label={t("prof_more")}
                      disabled={household !== null && household >= HOUSEHOLD_MAX}
                      onClick={() => patch({ household_size: Math.min(HOUSEHOLD_MAX, (household ?? 0) + 1) })}
                    >
                      <Icon name="plus" size={18} />
                    </button>
                  </div>
                </div>
                <div className="switch-list">
                  <SwitchRow label={t("prof_children")} value={profile.has_children} onChange={(v) => patch({ has_children: v })} />
                  <SwitchRow label={t("prof_pets")} value={profile.has_pets} onChange={(v) => patch({ has_pets: v })} />
                  {profile.has_pets && (
                    <label className="field nested">
                      {t("prof_pets_details")}
                      <input
                        className="input"
                        maxLength={300}
                        placeholder={t("prof_pets_placeholder")}
                        value={profile.pets_details ?? ""}
                        onChange={(e) => patch({ pets_details: e.target.value })}
                      />
                    </label>
                  )}
                  <SwitchRow label={t("prof_smoker")} value={profile.smoker} onChange={(v) => patch({ smoker: v })} />
                </div>
              </>
            )}
          </section>
        </>
      )}

      {tab === "account" && (
      <section className="card group tab-panel" key="account">
        <div className="info-row">
          <Icon name="user" size={20} />
          <span>{t("prof_phone_row")}</span>
          <span className="hint small">{user.has_phone ? t("prof_phone_yes") : t("prof_phone_no")}</span>
        </div>
        <button className="info-row danger" onClick={deleteAccount} disabled={busy}>
          <Icon name="trash" size={20} />
          <span>{t("prof_delete")}</span>
        </button>
      </section>
      )}
        </div>
      </div>

      <p className="hint small center-text">{t("prof_beta_note")}</p>

      {(dirty || message || error) && (
        <div className="sticky-actions">
          {error && <p className="error">{error}</p>}
          {message && !dirty && <p className="ok">{message}</p>}
          {dirty && (
            <button className="button" onClick={save} disabled={busy}>
              <Icon name="check" size={20} />
              {busy ? t("saving") : t("prof_save_all")}
            </button>
          )}
        </div>
      )}
    </Screen>
  );
}
