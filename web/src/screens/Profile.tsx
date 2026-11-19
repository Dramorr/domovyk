// PROF-1 (BOT_FLOWS §16, FR-USR-3, FR-USR-5, ADR 0016).
// Шапка з аватаром (тап — завантажити фото), розділи акордеонами:
// Персональні дані · Відгуки · Акаунт. Одна кнопка «Зберегти» внизу — для всіх змін.

import { useEffect, useRef, useState } from "react";

import { api, type City, type Personal, type User } from "../api/client";
import { t } from "../i18n";
import { confirm, type WebApp } from "../telegram";
import { Icon } from "../icons";
import { Avatar, Divider, errorCode, errorField, errorText, Fold, MascotTip, Screen } from "../ui";
import { isValidName } from "./Onboarding";

const AVATAR_MAX_MB = 15; // як avatar_max_bytes на бекенді

type Form = { legal_name: string; birth_date: string; city_code: string };

function toForm(p: Personal | null): Form {
  return { legal_name: p?.legal_name ?? "", birth_date: p?.birth_date ?? "", city_code: p?.city_code ?? "" };
}

/** +380670000001 → +380 67 000 00 01 */
function formatPhone(raw: string): string {
  const m = /^\+380(\d{2})(\d{3})(\d{2})(\d{2})$/.exec(raw);
  return m ? `+380 ${m[1]} ${m[2]} ${m[3]} ${m[4]}` : raw;
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
  const [name, setName] = useState(user.display_name);
  const [personal, setPersonal] = useState<Personal | null>(null);
  const [form, setForm] = useState<Form>(toForm(null));
  const [cities, setCities] = useState<City[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Аватар на телефоні: перший тап показує підказку, другий — вибір фото.
  const [armed, setArmed] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api
      .personal()
      .then((p) => {
        setPersonal(p);
        setForm(toForm(p));
      })
      .catch((e: unknown) => setError(errorText(e)));
    api
      .cities()
      .then(setCities)
      .catch(() => setCities([]));
  }, []);

  const nameChanged = name.trim() !== user.display_name;
  const saved = toForm(personal);
  const personalChanged =
    !!personal &&
    (form.legal_name.trim() !== saved.legal_name || form.birth_date !== saved.birth_date || form.city_code !== saved.city_code);
  const dirty = nameChanged || personalChanged;

  function edit(patch: Partial<Form>) {
    setForm((f) => ({ ...f, ...patch }));
    setMessage(null);
    setError(null);
  }

  function saveError(e: unknown): string {
    if (errorCode(e) === "too_young") return t("prof_too_young");
    const field = errorField(e);
    if (field === "legal_name") return t("prof_bad_legal_name");
    if (field === "birth_date") return t("prof_bad_birth_date");
    if (field === "city_code") return t("prof_bad_city");
    return errorText(e);
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
      if (personalChanged) {
        const p = await api.savePersonal({
          legal_name: form.legal_name.trim() || null,
          birth_date: form.birth_date || null,
          city_code: form.city_code || null,
        });
        setPersonal(p);
        setForm(toForm(p));
      }
      if (nameChanged) onUserChange(await api.rename(name.trim()));
      setMessage(t("prof_saved"));
      window.setTimeout(() => setMessage(null), 2500);
    } catch (e) {
      setError(saveError(e));
    } finally {
      setBusy(false);
    }
  }

  function onAvatarTap() {
    if (uploading) return;
    // Миша: одразу вибір файлу (підказку видно при наведенні). Палець: спершу підказка.
    if (armed || !window.matchMedia("(pointer: coarse)").matches) {
      setArmed(false);
      fileInput.current?.click();
    } else {
      setArmed(true);
      window.setTimeout(() => setArmed(false), 4000);
    }
  }

  async function onAvatarFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > AVATAR_MAX_MB * 1024 * 1024) return setError(t("ph_err_too_big"));
    setUploading(true);
    setError(null);
    try {
      const data = await file.arrayBuffer(); // одразу: на телефоні доступ до файлу тимчасовий
      onUserChange(await api.uploadAvatar(new Blob([data], { type: file.type || "image/jpeg" })));
    } catch (err) {
      setError(errorField(err) === "file" ? t("prof_avatar_bad") : errorText(err));
    } finally {
      setUploading(false);
    }
  }

  async function removeAvatar() {
    if (!(await confirm(webApp, t("prof_avatar_remove_confirm")))) return;
    try {
      onUserChange(await api.removeAvatar());
    } catch (e) {
      setError(errorText(e));
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

  const roleLabel = user.role === "admin" ? t("role_admin") : user.role === "moderator" ? t("role_moderator") : null;

  return (
    <Screen onBack={onBack} title={t("prof_title")}>
      <section className="profile-head">
        <div className="avatar-wrap">
          <button
            type="button"
            className={`avatar-button${armed ? " armed" : ""}${uploading ? " uploading" : ""}`}
            aria-label={t("prof_avatar_change")}
            onClick={onAvatarTap}
          >
            <Avatar userId={user.id} version={user.avatar_version} name={name || user.display_name} size={72} />
            <span className="avatar-overlay" aria-hidden="true">
              {uploading ? <span className="spinner" /> : <Icon name="upload" size={24} />}
            </span>
          </button>
          {roleLabel && (
            <span className="role-badge" title={roleLabel} aria-label={roleLabel}>
              <Icon name="shield" size={14} />
            </span>
          )}
          <input ref={fileInput} type="file" accept="image/*" hidden onChange={onAvatarFile} />
        </div>
        <div className="profile-head-text">
          <p className="profile-name">{name.trim() || user.display_name}</p>
          {armed && <p className="hint small">{t("prof_avatar_tap_again")}</p>}
          {!armed && user.avatar_version && (
            <button className="link small" onClick={removeAvatar}>
              {t("prof_avatar_remove")}
            </button>
          )}
        </div>
      </section>

      {/* Розділи — акордеони: заголовок з підсумком, вміст по тапу. */}
      <Fold
        id="profile:personal"
        icon="user"
        defaultOpen
        title={t("prof_tab_personal")}
        aside={dirty ? <span className="tab-dot" aria-label={t("pf_unsaved")} /> : undefined}
      >
        <label className="field">
          {t("prof_name")}
          <input
            className="input"
            value={name}
            maxLength={40}
            autoComplete="nickname"
            onChange={(e) => {
              setName(e.target.value);
              setMessage(null);
            }}
          />
        </label>
        <p className="hint small">{t("prof_name_hint")}</p>
        {!personal && !error && <div className="skeleton" />}
        {personal && (
          <>
            <label className="field">
              {t("prof_legal_name")}
              <input
                className="input"
                value={form.legal_name}
                maxLength={150}
                autoComplete="name"
                placeholder={t("prof_legal_name_placeholder")}
                onChange={(e) => edit({ legal_name: e.target.value })}
              />
            </label>
            <p className="hint small">{t("prof_legal_name_hint")}</p>
            <label className="field">
              {t("prof_birth_date")}
              <input
                className="input"
                type="date"
                value={form.birth_date}
                min="1900-01-01"
                max={new Date().toISOString().slice(0, 10)}
                onChange={(e) => edit({ birth_date: e.target.value })}
              />
            </label>
            <p className="hint small">{t("prof_birth_date_hint")}</p>
            <label className="field">
              {t("prof_city")}
              <select className="input" value={form.city_code} onChange={(e) => edit({ city_code: e.target.value })}>
                <option value="">{t("prof_city_none")}</option>
                {cities.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="info-row phone-row">
              <span className="info-label">{t("prof_phone_row")}</span>
              <span className="phone-value">{personal.phone ? formatPhone(personal.phone) : "—"}</span>
              {user.has_phone ? (
                <span className="phone-ok" title={t("prof_phone_yes")} aria-label={t("prof_phone_yes")}>
                  <Icon name="check" size={18} />
                </span>
              ) : (
                <span className="phone-bad" title={t("prof_phone_no")} aria-label={t("prof_phone_no")}>
                  <Icon name="alert" size={18} />
                </span>
              )}
            </div>
            {!user.has_phone && <p className="hint small">{t("prof_phone_need")}</p>}
          </>
        )}
      </Fold>

      <Fold id="profile:reviews" icon="star" title={t("prof_tab_reviews")}>
        <MascotTip>
          <p>{t("prof_reviews_empty")}</p>
        </MascotTip>
      </Fold>

      <Fold id="profile:account" icon="shield" title={t("prof_tab_account")}>
        <div className="account-panel">
          <div className="info-row">
            <Icon name="send" size={20} />
            <span>{t("prof_login_telegram")}</span>
          </div>
          <p className="hint small">{t("prof_login_hint")}</p>
        </div>
      </Fold>

      <Divider />
      <button className="link danger" onClick={deleteAccount} disabled={busy}>
        {t("prof_delete")}
      </button>

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
