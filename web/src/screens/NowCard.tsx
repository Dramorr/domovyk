// Картка «Зараз» (ADR 0023): одна наступна дія, яку рахує бекенд (domain/overview.py).
// Кнопка — лише коли дія в іншому місці; якщо вона прямо нижче на «Огляді», кнопки немає.

import type { NowOut } from "../api/client";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import { formatDate } from "../ui";

export type NowTarget = NowOut["target"];

export function NowCard({ now, onTarget }: { now: NowOut; onTarget: (target: NowTarget) => void }) {
  const date = now.on ? formatDate(now.on) : "";
  // Без дати — коротший варіант тексту (напр. «Усе гаразд.» без «наступна оплата …»).
  const key = (now.on || now.code !== "all_good" ? `now_${now.code}` : "now_all_good_plain") as TextKey;
  const text = t(key, { count: String(now.count ?? 0), date });
  const button = now.target !== "none" && now.target !== "overview";
  return (
    <section className={`card now-card tone-${now.tone}`}>
      <span className="now-label">{t("now_label")}</span>
      <p className="now-text">{text}</p>
      {button && (
        <button className={now.tone === "action" ? "button" : "button secondary"} onClick={() => onTarget(now.target)}>
          {t(`now_btn_${now.target}` as TextKey)}
          <Icon name="chevron" size={18} />
        </button>
      )}
    </section>
  );
}
