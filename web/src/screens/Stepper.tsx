// Покрокові дії (створення об'єкта й подібне) — той самий слайдер, що на вході (Onboarding):
// крапки прогресу, анімований перехід, свайп назад, «Пропустити» для необов'язкових кроків.

import { useEffect, useRef, useState, type ReactNode, type TouchEvent } from "react";

import { t } from "../i18n";
import { Icon } from "../icons";
import { Screen } from "../ui";

export type Step = {
  key: string;
  title: string;
  text?: string;
  content: ReactNode;
  /** Головна кнопка внизу. Немає — крок іде далі сам (вибір зі списку). */
  action?: { label: string; onClick: () => void; disabled?: boolean; final?: boolean; secondary?: boolean };
  /** Необов'язковий крок: «Пропустити» вгорі й підказка під кнопкою. */
  skip?: { onClick: () => void; note?: string };
};

const SWIPE_PX = 48;

/** Неактивні кроки недоступні з клавіатури. Типи React 18 ще не знають `inert`. */
function inert(on: boolean): object {
  return on ? { inert: "" } : {};
}

export function Stepper({
  steps,
  at,
  onGo,
  onExit,
  title,
  error,
}: {
  steps: Step[];
  at: number;
  onGo: (index: number) => void;
  onExit: () => void;
  title?: string;
  error?: string | null;
}) {
  const touchX = useRef<number | null>(null);
  const [reached, setReached] = useState(at);
  useEffect(() => setReached((r) => Math.max(r, at)), [at]);
  const step = steps[at];
  const back = () => (at > 0 ? onGo(at - 1) : onExit());

  function onTouchStart(e: TouchEvent) {
    touchX.current = e.touches[0]?.clientX ?? null;
  }

  function onTouchEnd(e: TouchEvent) {
    const from = touchX.current;
    const to = e.changedTouches[0]?.clientX;
    touchX.current = null;
    if (from === null || to === undefined || Math.abs(to - from) < SWIPE_PX) return;
    // Уперед — лише на вже пройдені кроки: нові відкриваються кнопкою, після перевірки полів.
    if (to > from && at > 0) onGo(at - 1);
    if (to < from && at < reached) onGo(at + 1);
  }

  if (!step) return null;
  return (
    <Screen onBack={back} title={title}>
      <div className="intro stepper" onTouchStart={onTouchStart} onTouchEnd={onTouchEnd}>
        <div className="intro-top stepper-top">
          <span className="hint small">{t("wiz_step", { n: String(at + 1), total: String(steps.length) })}</span>
          {step.skip && (
            <button className="link" onClick={step.skip.onClick}>
              {t("wiz_skip")}
            </button>
          )}
        </div>

        <div
          className="intro-viewport"
          // Запасний захист для старих WebView без overflow: clip: браузер прокручує
          // контейнер до поля у фокусі, і крок «з'їжджає» вбік.
          onScroll={(e) => {
            if (e.currentTarget.scrollLeft) e.currentTarget.scrollLeft = 0;
          }}
        >
          <div className="intro-track" style={{ transform: `translateX(-${at * 100}%)` }}>
            {steps.map((s, i) => (
              <section
                key={s.key}
                className={`intro-slide${i === at ? " current" : ""}`}
                aria-hidden={i !== at}
                {...inert(i !== at)}
              >
                <h1>{s.title}</h1>
                {s.text && <p className="hint">{s.text}</p>}
                {s.content}
              </section>
            ))}
          </div>
        </div>

        <div className="intro-bottom">
          <div className="intro-dots">
            {steps.map((s, i) => (
              <button
                key={s.key}
                className={`intro-dot${i === at ? " active" : ""}`}
                aria-current={i === at ? "step" : undefined}
                aria-label={t("onb_intro_goto", { n: String(i + 1) })}
                disabled={i > reached}
                onClick={() => onGo(i)}
              />
            ))}
          </div>
          {error && <p className="error">{error}</p>}
          {step.action && (
            <button
              className={step.action.secondary ? "button secondary" : "button"}
              disabled={step.action.disabled}
              onClick={step.action.onClick}
            >
              {step.action.label}
              {!step.action.final && !step.action.secondary && !step.action.disabled && <Icon name="chevron" size={20} />}
            </button>
          )}
          {step.skip?.note && <p className="hint small center-text">{step.skip.note}</p>}
        </div>
      </div>
    </Screen>
  );
}
