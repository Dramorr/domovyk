// PROF-1 і PROF-TENANT (BOT_FLOWS §16, FR-USR-3, FR-USR-5).

import { useEffect, useState } from "react";

import { api, type TenantProfile, type User } from "../api/client";
import { t } from "../i18n";
import { confirm, type WebApp } from "../telegram";
import { Icon } from "../icons";
import { Chip, errorCode, errorText, Screen } from "../ui";
import { isValidName } from "./Onboarding";

type Tri = boolean | null | undefined;

function TriToggle({ label, value, onChange }: { label: string; value: Tri; onChange: (v: boolean) => void }) {
  return (
    <label className="check">
      <input type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

export function Profile({
  user,
  webApp,
  onBack,
  onUserChange,
  onDeleted,
  onSwitchMode,
}: {
  user: User;
  webApp: WebApp;
  onBack?: () => void;
  onUserChange: (user: User) => void;
  onDeleted: () => void;
  onSwitchMode: () => void;
}) {
  const [name, setName] = useState(user.display_name);
  const [profile, setProfile] = useState<TenantProfile | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.tenantProfile().then(setProfile).catch((e: unknown) => setError(errorText(e)));
  }, []);

  function patch(update: Partial<TenantProfile>) {
    setProfile((p) => ({ ...(p ?? {}), ...update }));
    setMessage(null);
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await action();
      setMessage(t("prof_saved"));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveName() {
    if (!isValidName(name)) {
      setError(t("onb_name_invalid"));
      return;
    }
    await run(async () => onUserChange(await api.rename(name)));
  }

  async function saveProfile() {
    if (!profile) return;
    await run(async () => setProfile(await api.saveTenantProfile(profile)));
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

  return (
    <Screen onBack={onBack}>
      <h1>{t("prof_title")}</h1>
      <div className="chips">
        {user.has_phone && <Chip tone="ok">✓ {t("prof_phone_ok")}</Chip>}
        {user.role !== "user" && <Chip tone="warn">{t(`role_${user.role}`)}</Chip>}
      </div>

      <button className="mode-card" onClick={onSwitchMode}>
        <span className="row-icon">
          <Icon name="swap" />
        </span>
        <span className="row-body">
          <span className="row-title">{user.mode === "landlord" ? t("mode_card_to_tenant") : t("mode_card_to_landlord")}</span>
          <span className="row-sub">{user.mode === "landlord" ? t("mode_card_to_tenant_sub") : t("mode_card_to_landlord_sub")}</span>
        </span>
      </button>

      <section className="card">
        <label className="field">
          {t("prof_name")}
          <input className="input" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
        </label>
        <button className="button secondary" onClick={saveName} disabled={busy || name === user.display_name}>
          {t("prof_save_name")}
        </button>
      </section>

      {user.mode !== "landlord" && (
      <section className="card">
        <h2>{t("prof_tenant_title")}</h2>
        <p className="hint small">{t("prof_tenant_hint")}</p>
        {profile && (
          <>
            <label className="field">
              {t("prof_about")}
              <textarea
                className="input"
                rows={3}
                maxLength={1000}
                value={profile.about ?? ""}
                onChange={(e) => patch({ about: e.target.value })}
              />
            </label>
            <label className="field">
              {t("prof_occupation")}
              <input
                className="input"
                maxLength={100}
                value={profile.occupation ?? ""}
                onChange={(e) => patch({ occupation: e.target.value })}
              />
            </label>
            <label className="field">
              {t("prof_household")}
              <input
                className="input"
                type="number"
                min={1}
                max={20}
                value={profile.household_size ?? ""}
                onChange={(e) =>
                  patch({ household_size: e.target.value ? Number(e.target.value) : null })
                }
              />
            </label>
            <TriToggle label={t("prof_children")} value={profile.has_children} onChange={(v) => patch({ has_children: v })} />
            <TriToggle label={t("prof_pets")} value={profile.has_pets} onChange={(v) => patch({ has_pets: v })} />
            {profile.has_pets && (
              <label className="field">
                {t("prof_pets_details")}
                <input
                  className="input"
                  maxLength={300}
                  value={profile.pets_details ?? ""}
                  onChange={(e) => patch({ pets_details: e.target.value })}
                />
              </label>
            )}
            <TriToggle label={t("prof_smoker")} value={profile.smoker} onChange={(v) => patch({ smoker: v })} />
            <button className="button" onClick={saveProfile} disabled={busy}>
              {busy ? t("saving") : t("prof_save")}
            </button>
          </>
        )}
      </section>
      )}

      {message && <p className="ok">{message}</p>}
      {error && <p className="error">{error}</p>}

      <p className="hint small center-text">{t("prof_beta_note")}</p>

      <button className="link danger" onClick={deleteAccount} disabled={busy}>
        {t("prof_delete")}
      </button>
    </Screen>
  );
}
