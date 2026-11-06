// Онбординг (BOT_FLOWS §1, FR-USR-1/7): знайомство (слайди, згода на останньому) → ім'я → номер.

import { useEffect, useRef, useState, type TouchEvent } from "react";

import { api, ApiFailure, type Me, type User } from "../api/client";
import { t, type TextKey } from "../i18n";
import { openLink, requestContact, type WebApp } from "../telegram";
import { Icon, type IconName } from "../icons";
import { BRAND, errorText, Mascot, Screen } from "../ui";

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
  const [step, setStep] = useState<"intro" | "name">("intro");
  const [introAt, setIntroAt] = useState(0);
  const [name, setName] = useState(me.telegram.first_name);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (step === "intro") {
    return (
      <Intro
        startAt={introAt}
        error={error}
        onTerms={() => openLink(webApp, me.consent.terms_url)}
        onPrivacy={() => openLink(webApp, me.consent.privacy_url)}
        onAccept={() => {
          setError(null);
          setStep("name");
        }}
      />
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
        setIntroAt(SLIDES.length);
        setStep("intro");
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
    <Screen
      onBack={() => {
        setIntroAt(SLIDES.length);
        setStep("intro");
      }}
    >
      <h1 className="center-text">{t("onb_name_title")}</h1>
      <p className="hint center-text">{t("onb_name_text")}</p>
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

// ---------------------------------------------------------------- знайомство (ONB-0, FR-USR-7)

type SlideKey = "welcome" | "tenant" | "landlord";
type Slide = { key: SlideKey; icon?: IconName };

/** Слайди про можливості. Тексти — у uk.json (`onb_intro_<key>_title/_text`). */
const SLIDES: Slide[] = [
  { key: "welcome" },
  { key: "tenant", icon: "search" },
  { key: "landlord", icon: "building" },
];

const SWIPE_PX = 48;

/** Неактивні слайди недоступні з клавіатури. Типи React 18 ще не знають `inert`. */
function inert(on: boolean): object {
  return on ? { inert: "" } : {};
}

function SlideArt({ icon, lantern = false }: { icon?: IconName; lantern?: boolean }) {
  return (
    <div className="intro-art">
      <img src={`${BRAND}${lantern ? "mascot-lantern" : "mascot"}.svg`} alt="" />
      {icon && (
        <span className="intro-badge">
          <Icon name={icon} size={26} />
        </span>
      )}
    </div>
  );
}

/** Кілька слайдів перед реєстрацією; останній — бета, правила й кнопка згоди. */
export function Intro({
  startAt = 0,
  error,
  onTerms,
  onPrivacy,
  onAccept,
}: {
  startAt?: number;
  error: string | null;
  onTerms: () => void;
  onPrivacy: () => void;
  onAccept: () => void;
}) {
  const last = SLIDES.length; // індекс фінального слайда (згода)
  const [at, setAt] = useState(Math.min(startAt, last));
  const touchX = useRef<number | null>(null);
  const go = (i: number) => setAt(Math.max(0, Math.min(last, i)));

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "ArrowRight") setAt((i) => Math.min(last, i + 1));
      if (e.key === "ArrowLeft") setAt((i) => Math.max(0, i - 1));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [last]);

  function onTouchStart(e: TouchEvent) {
    touchX.current = e.touches[0]?.clientX ?? null;
  }

  function onTouchEnd(e: TouchEvent) {
    const from = touchX.current;
    const to = e.changedTouches[0]?.clientX;
    touchX.current = null;
    if (from === null || to === undefined || Math.abs(to - from) < SWIPE_PX) return;
    go(to < from ? at + 1 : at - 1);
  }

  const dots = [...SLIDES.map((sl) => sl.key as string), "final"];

  return (
    <Screen>
      <div className="intro" onTouchStart={onTouchStart} onTouchEnd={onTouchEnd}>
        <div className="intro-top">
          {at < last && (
            <button className="link" onClick={() => go(last)}>
              {t("onb_intro_skip")}
            </button>
          )}
        </div>

        <div className="intro-viewport" aria-roledescription={t("onb_intro_carousel")}>
          <div className="intro-track" style={{ transform: `translateX(-${at * 100}%)` }}>
            {SLIDES.map((sl, i) => (
              <section
                key={sl.key}
                className="intro-slide"
                aria-roledescription={t("onb_intro_slide")}
                aria-hidden={i !== at}
                {...inert(i !== at)}
              >
                <SlideArt icon={sl.icon} />
                <h1>{t(`onb_intro_${sl.key}_title` as TextKey)}</h1>
                <p>{t(`onb_intro_${sl.key}_text` as TextKey)}</p>
              </section>
            ))}
            <section
              className="intro-slide"
              aria-roledescription={t("onb_intro_slide")}
              aria-hidden={at !== last}
              {...inert(at !== last)}
            >
              <SlideArt icon="shield" lantern />
              <h1>{t("onb_intro_final_title")}</h1>
              <p>
                <span className="badge">{t("onb_intro_beta")}</span> {t("onb_beta_note")}
              </p>
              <p>{t("onb_consent_text")}</p>
              <p className="links">
                <button className="link" onClick={onTerms}>
                  {t("onb_terms")}
                </button>
                {" · "}
                <button className="link" onClick={onPrivacy}>
                  {t("onb_privacy")}
                </button>
              </p>
            </section>
          </div>
        </div>

        <div className="intro-bottom">
          <div className="intro-dots">
            {dots.map((key, i) => (
              <button
                key={key}
                className={`intro-dot${i === at ? " active" : ""}`}
                aria-current={i === at ? "step" : undefined}
                aria-label={t("onb_intro_goto", { n: String(i + 1) })}
                onClick={() => go(i)}
              />
            ))}
          </div>
          {error && <p className="error">{error}</p>}
          {at < last ? (
            <button className="button" onClick={() => go(at + 1)}>
              {t("onb_intro_next")} <Icon name="chevron" size={20} />
            </button>
          ) : (
            <button className="button" onClick={onAccept}>
              {t("onb_accept")}
            </button>
          )}
        </div>
      </div>
    </Screen>
  );
}

// ---------------------------------------------------------------- номер телефону

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
      <h1 className="center-text">{t("onb_phone_title")}</h1>
      <p className="center-text">{t("onb_phone_text")}</p>
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

// ONB-4 · «Що тебе привело?» (ADR 0006): режим, а не тип акаунта — змінюється будь-коли.
export function ModeChoice({ onDone }: { onDone: (user: User) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function choose(mode: "tenant" | "landlord") {
    setBusy(true);
    setError(null);
    try {
      onDone(await api.setMode(mode));
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <Screen>
      <Mascot size="md" />
      <h1 className="center-text">{t("mode_question")}</h1>
      <p className="hint center-text">{t("mode_question_hint")}</p>
      <div className="mode-cards">
        <button className="choice-big" disabled={busy} onClick={() => choose("tenant")}>
          <span className="row-icon">
            <Icon name="search" />
          </span>
          {t("mode_tenant_title")}
          <span className="quick-sub">{t("mode_tenant_sub")}</span>
        </button>
        <button className="choice-big" disabled={busy} onClick={() => choose("landlord")}>
          <span className="row-icon">
            <Icon name="building" />
          </span>
          {t("mode_landlord_title")}
          <span className="quick-sub">{t("mode_landlord_sub")}</span>
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </Screen>
  );
}
