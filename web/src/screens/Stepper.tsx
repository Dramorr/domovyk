// Покрокові дії (створення об'єкта й подібне) — той самий слайдер, що на вході (Onboarding):
// крапки прогресу, анімований перехід, свайп назад. На кожному кроці Домовик визирає
// з-за кнопки щоразу з іншого місця — збоку від крапок, щоб не закривати форму.

import { useEffect, useRef, useState, type ReactNode, type TouchEvent } from "react";

import { t } from "../i18n";
import { Icon, type IconName } from "../icons";
import { BRAND, Screen } from "../ui";

export type Step = {
  key: string;
  title: string;
  text?: string;
  content: ReactNode;
  /** Головна кнопка внизу. Немає — крок іде далі сам (вибір зі списку). */
  action?: {
    label: string;
    onClick: () => void;
    disabled?: boolean;
    /** Останній крок: без стрілки, можна з іконкою. */
    final?: boolean;
    /** Другорядна дія (напр. «Пропустити»): контурна кнопка. */
    secondary?: boolean;
    icon?: IconName;
  };
};

const SWIPE_PX = 48;

/** Де визирає Домовик: збоку від крапок, по черзі ліворуч і праворуч. */
const PEEK = ["peek-1", "peek-2", "peek-3", "peek-4"];

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
  open = false,
}: {
  steps: Step[];
  at: number;
  onGo: (index: number) => void;
  onExit: () => void;
  title?: string;
  error?: string | null;
  /** Усі кроки вже заповнені (зміна наявного): крапками й свайпом можна йти куди завгодно. */
  open?: boolean;
}) {
  const touchX = useRef<number | null>(null);
  const [reached, setReached] = useState(open ? steps.length - 1 : at);
  useEffect(() => setReached((r) => Math.max(r, at)), [at]);
  const step = steps[at];
  const last = steps.length - 1;
  // Зміна наявного: «Назад» з розділу — до підсумку, з підсумку — вихід.
  const back = () => (open ? (at === last ? onExit() : onGo(last)) : at > 0 ? onGo(at - 1) : onExit());

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
  const action = step.action;
  return (
    <Screen onBack={back} title={title}>
      <div className="intro stepper" onTouchStart={onTouchStart} onTouchEnd={onTouchEnd}>
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
          <div className="stepper-action">
            <img key={at} className={`peek ${PEEK[at % PEEK.length]}`} src={`${BRAND}mascot.svg`} alt="" />
            {action && (
              <button
                className={action.secondary ? "button outline" : "button"}
                disabled={action.disabled}
                onClick={action.onClick}
              >
                {action.icon && <Icon name={action.icon} size={20} />}
                {action.label}
                {!action.final && !action.disabled && <Icon name="chevron" size={20} />}
              </button>
            )}
          </div>
        </div>
      </div>
    </Screen>
  );
}
