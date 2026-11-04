// Онбординг (BOT_FLOWS §1, FR-USR-1): згода → ім'я → номер телефону.

import { useState } from "react";

import { api, ApiFailure, type Me, type User } from "../api/client";
import { t } from "../i18n";
import { openLink, requestContact, type WebApp } from "../telegram";
import { errorText, Mascot, Screen } from "../ui";

const NAME_RE = /^\p{L}[\p{L} '’ʼ-]*$/u;

export function isValidName(raw: string): boolean {
  const name = raw.trim().replace(/\s+/g, " ");
  return name.length >= 2 && name.length <= 40 && NAME_RE.test(name);
}

export function ConsentAndName({
  me,
  webApp,
  onRegistered,
}: {
  me: Me;
  webApp: WebApp;
  onRegistered: (user: User) => void;
}) {
  const [step, setStep] = useState<"consent" | "name">("consent");
  const [name, setName] = useState(me.telegram.first_name);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (step === "consent") {
    return (
      <Screen>
        <Mascot />
        <h1>{t("onb_welcome_title")}</h1>
        <p>{t("onb_welcome_text")}</p>
        <p className="hint">{t("onb_beta_note")}</p>
        <p>{t("onb_consent_text")}</p>
        <p className="links">
          <button className="link" onClick={() => openLink(webApp, me.consent.terms_url)}>
            {t("onb_terms")}
          </button>
          {" · "}
          <button className="link" onClick={() => openLink(webApp, me.consent.privacy_url)}>
            {t("onb_privacy")}
          </button>
        </p>
        {error && <p className="error">{error}</p>}
        <button className="button" onClick={() => setStep("name")}>
          {t("onb_accept")}
        </button>
      </Screen>
    );
  }

  async function submit() {
    if (!isValidName(name)) {
      setError(t("onb_name_invalid"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const user = await api.register({
        display_name: name,
        terms_version: me.consent.terms_version,
        privacy_version: me.consent.privacy_version,
      });
      onRegistered(user);
    } catch (e) {
      if (e instanceof ApiFailure && e.code === "consent_outdated") {
        setStep("consent");
        setError(t("onb_consent_outdated"));
      } else if (e instanceof ApiFailure && e.code === "invalid_input") {
        setError(t("onb_name_invalid"));
      } else if (e instanceof ApiFailure && e.code === "already_registered") {
        window.location.reload();
      } else {
        setError(errorText(e));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen onBack={() => setStep("consent")}>
      <h1>{t("onb_name_title")}</h1>
      <p className="hint">{t("onb_name_text")}</p>
      <input
        className="input"
        value={name}
        maxLength={40}
        placeholder={t("onb_name_placeholder")}
        onChange={(e) => setName(e.target.value)}
        autoFocus
      />
      {error && <p className="error">{error}</p>}
      <button className="button" onClick={submit} disabled={busy}>
        {busy ? t("saving") : t("onb_name_next")}
      </button>
    </Screen>
  );
}

const POLL_MS = 1500;
const POLL_TRIES = 12;

export function PhoneStep({ webApp, onDone }: { webApp: WebApp; onDone: (user: User) => void }) {
  const [state, setState] = useState<"idle" | "waiting" | "declined" | "timeout" | "error">(
    "idle",
  );
  const [error, setError] = useState<string | null>(null);

  async function share() {
    setError(null);
    const shared = await requestContact(webApp);
    if (!shared) {
      setState("declined");
      return;
    }
    // Номер приходить боту повідомленням від Telegram; чекаємо, поки бекенд його збереже.
    setState("waiting");
    for (let i = 0; i < POLL_TRIES; i++) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      try {
        const me = await api.me();
        if (me.user?.has_phone) {
          onDone(me.user);
          return;
        }
      } catch (e) {
        setState("error");
        setError(errorText(e));
        return;
      }
    }
    setState("timeout");
  }

  return (
    <Screen>
      <Mascot />
      <h1>{t("onb_phone_title")}</h1>
      <p>{t("onb_phone_text")}</p>
      {state === "waiting" && <p className="hint">{t("onb_phone_waiting")}</p>}
      {state === "declined" && <p className="error">{t("onb_phone_declined")}</p>}
      {state === "timeout" && <p className="error">{t("onb_phone_timeout")}</p>}
      {error && <p className="error">{error}</p>}
      <button className="button" onClick={share} disabled={state === "waiting"}>
        {t("onb_phone_button")}
      </button>
      <p className="hint small">{t("onb_phone_fallback")}</p>
    </Screen>
  );
}
