// Покази (M6.5, BOT_FLOWS §6, FR-APP-4, FR-APP-5, ADR 0022). Власник додає час і місткість
// на сторінці оголошення; погоджений орендар записується в заявці. Після показу обидва кажуть,
// чи він відбувся. Нагадування за 2 години й питання «як пройшло» приходять у бот.

import { useEffect, useState } from "react";

import { api, type ApplicationCard, type ShowingAnswer, type ShowingsBoard, type SlotChoice } from "../api/client";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import { confirm, type WebApp } from "../telegram";
import { Chip, errorReason, errorText, Fold, MascotTip, Sheet, SheetHead } from "../ui";
import { Chips } from "./Leases";

const k = (key: string, vars?: Record<string, string>) => t(key as TextKey, vars);

const DURATIONS = [15, 30, 45, 60];
const CAPACITIES = [1, 2, 3, 5, 10];
const ANSWERS: ShowingAnswer[] = ["done", "no_show", "moved"];

/** «чт, 19 листопада · 11:00–11:30» за Києвом. */
export function formatSlot(starts: string, ends: string): string {
  const tz = { timeZone: "Europe/Kyiv" } as const;
  const day = new Date(starts).toLocaleDateString("uk-UA", { ...tz, weekday: "short", day: "numeric", month: "long" });
  const hm = (iso: string) => new Date(iso).toLocaleTimeString("uk-UA", { ...tz, hour: "2-digit", minute: "2-digit" });
  return `${day} · ${hm(starts)}–${hm(ends)}`;
}

function kyivDate(offsetDays: number): string {
  const d = new Date(Date.now() + offsetDays * 86_400_000);
  return d.toLocaleDateString("sv-SE", { timeZone: "Europe/Kyiv" }); // YYYY-MM-DD
}

function slotError(e: unknown): string {
  switch (errorReason(e)) {
    case "too_soon":
      return t("sh_err_too_soon");
    case "too_far":
      return t("sh_err_too_far");
    case "overlap":
      return t("sh_err_overlap");
    case "too_many":
      return t("sh_err_too_many");
    case "full":
      return t("sh_err_full");
    case "past":
    case "started":
      return t("sh_err_started");
  }
  return errorText(e);
}

// ---------------------------------------------------------------- власник: оголошення

export function ShowingsBlock({ propertyId, webApp }: { propertyId: string; webApp: WebApp }) {
  const [board, setBoard] = useState<ShowingsBoard | null>(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .showingsBoard(propertyId)
      .then(setBoard)
      .catch(() => setBoard(null));
  }, [propertyId]);

  if (!board || !board.can_add) return null;
  const booked = board.slots.reduce((n, s) => n + s.booked.length, 0);

  async function cancel(slotId: string) {
    if (!(await confirm(webApp, t("sh_cancel_slot_confirm")))) return;
    setBusy(true);
    setError(null);
    try {
      await api.cancelSlot(slotId);
      setBoard(await api.showingsBoard(propertyId));
    } catch (e) {
      setError(slotError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Fold
      id={`showings:${propertyId}`}
      icon="clock"
      title={t("sh_title")}
      aside={booked > 0 ? <span className="count">{booked}</span> : undefined}
      defaultOpen
    >
      {board.slots.length === 0 ? (
        <p className="hint small">{t("sh_empty_landlord")}</p>
      ) : (
        <ul className="slot-list">
          {board.slots.map((s) => (
            <li key={s.id} className="slot-row">
              <div className="slot-main">
                <span className="row-title">{formatSlot(s.starts_at, s.ends_at)}</span>
                <span className="hint small">
                  {s.booked.length > 0 ? s.booked.map((b) => b.display_name).join(", ") : t("sh_nobody")}
                  {s.capacity > 1 && ` · ${k("sh_capacity_of", { n: String(s.booked.length), cap: String(s.capacity) })}`}
                </span>
              </div>
              <button className="icon-button" aria-label={t("sh_cancel_slot")} disabled={busy} onClick={() => cancel(s.id)}>
                <Icon name="trash" size={18} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="error">{error}</p>}
      <button className="button secondary" onClick={() => setAdding(true)}>
        <Icon name="plus" size={20} />
        {t("sh_add")}
      </button>
      {adding && (
        <AddSlotSheet
          propertyId={propertyId}
          onClose={() => setAdding(false)}
          onSaved={(b) => {
            setBoard(b);
            setAdding(false);
          }}
        />
      )}
    </Fold>
  );
}

function AddSlotSheet({
  propertyId,
  onClose,
  onSaved,
}: {
  propertyId: string;
  onClose: () => void;
  onSaved: (b: ShowingsBoard) => void;
}) {
  const [day, setDay] = useState(kyivDate(1));
  const [start, setStart] = useState("18:00");
  const [minutes, setMinutes] = useState(30);
  const [capacity, setCapacity] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.addSlot(propertyId, { day, start, minutes, capacity }));
    } catch (e) {
      setError(slotError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet label={t("sh_add_title")} onClose={onClose}>
      {(close) => (
        <>
          <SheetHead title={t("sh_add_title")} onClose={close} />
          <div className="pair">
            <label className="field">
              {t("sh_day")}
              <input className="input" type="date" value={day} min={kyivDate(0)} max={kyivDate(60)} onChange={(e) => setDay(e.target.value)} />
            </label>
            <label className="field">
              {t("sh_start")}
              <input className="input" type="time" value={start} step={300} onChange={(e) => setStart(e.target.value)} />
            </label>
          </div>
          <div className="field">
            {t("sh_minutes")}
            <Chips<number> value={minutes} options={DURATIONS} label={(m) => k("sh_minutes_n", { n: String(m) })} onChange={setMinutes} />
          </div>
          <div className="field">
            {t("sh_capacity")}
            <Chips<number> value={capacity} options={CAPACITIES} label={(n) => String(n)} onChange={setCapacity} />
          </div>
          <p className="hint small">{capacity > 1 ? t("sh_group_hint") : t("sh_single_hint")}</p>
          {error && <p className="error">{error}</p>}
          <button className="button" disabled={busy || !day || !start} onClick={save}>
            <Icon name="check" size={20} />
            {t("sh_add_save")}
          </button>
        </>
      )}
    </Sheet>
  );
}

// ------------------------------------------------------------------- обидва: заявка

/** Блок «Показ» у картці заявки: обрати час, запис, «як пройшло». */
export function ApplicationShowing({
  card,
  webApp,
  onCard,
  onManageListing,
}: {
  card: ApplicationCard;
  webApp: WebApp;
  onCard: (c: ApplicationCard) => void;
  onManageListing: () => void;
}) {
  const [choosing, setChoosing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const landlord = card.my_role === "landlord";
  const sh = card.showing;
  const relevant = ["approved", "showing_scheduled", "showing_done"].includes(card.status);
  if (!relevant) return null;

  async function run(action: () => Promise<ApplicationCard>) {
    setBusy(true);
    setError(null);
    try {
      onCard(await action());
    } catch (e) {
      setError(slotError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <div className="stat">
        <h2 style={{ margin: 0, flex: 1 }}>{t("sh_card_title")}</h2>
        {sh?.group && <Chip tone="info">{t("sh_group")}</Chip>}
      </div>

      {sh && card.status === "showing_scheduled" && (
        <>
          <p className="row-title slot-when">{formatSlot(sh.starts_at, sh.ends_at)}</p>
          {sh.can_answer && (
            <>
              <p className="hint small">{t("sh_how_was_it")}</p>
              <div className="answer-list">
                {ANSWERS.map((a) => (
                  <button key={a} className={a === "done" ? "button" : "button secondary"} disabled={busy} onClick={() => run(() => api.answerShowing(card.id, a))}>
                    {k(`sh_answer_${a}`)}
                  </button>
                ))}
              </div>
            </>
          )}
          {sh.my_answer && <p className="hint small">{k("sh_answered", { answer: k(`sh_answer_${sh.my_answer}`) })}</p>}
          {!sh.can_answer && !sh.my_answer && <p className="hint small">{t("sh_reminder_note")}</p>}
        </>
      )}
      {sh && card.status === "showing_done" && <p className="hint small">{k("sh_was", { when: formatSlot(sh.starts_at, sh.ends_at) })}</p>}

      {landlord && card.status === "approved" && (
        <button className="button secondary" onClick={onManageListing}>
          <Icon name="clock" size={20} />
          {t("sh_manage")}
        </button>
      )}

      {card.can_book && (
        <button className={card.status === "approved" ? "button" : "button secondary"} disabled={busy} onClick={() => setChoosing(true)}>
          <Icon name="clock" size={20} />
          {card.status === "approved" ? t("sh_choose") : card.status === "showing_done" ? t("sh_again") : t("sh_change")}
        </button>
      )}
      {card.can_cancel_showing && (
        <button
          className="link danger"
          disabled={busy}
          onClick={async () => {
            if (!(await confirm(webApp, t("sh_cancel_confirm")))) return;
            await run(() => api.cancelShowing(card.id));
          }}
        >
          {t("sh_cant_come")}
        </button>
      )}
      {error && <p className="error">{error}</p>}

      {choosing && (
        <ChooseSlotSheet
          card={card}
          onClose={() => setChoosing(false)}
          onBooked={(c) => {
            onCard(c);
            setChoosing(false);
          }}
        />
      )}
    </section>
  );
}

function ChooseSlotSheet({ card, onClose, onBooked }: { card: ApplicationCard; onClose: () => void; onBooked: (c: ApplicationCard) => void }) {
  const [slots, setSlots] = useState<SlotChoice[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .applicationSlots(card.id)
      .then(setSlots)
      .catch((e: unknown) => setError(errorText(e)));
  }, [card.id]);

  async function book(slotId: string) {
    setBusy(true);
    setError(null);
    try {
      onBooked(await api.bookShowing(card.id, slotId));
    } catch (e) {
      setError(slotError(e));
      setSlots(await api.applicationSlots(card.id).catch(() => slots));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet label={t("sh_choose_title")} onClose={onClose}>
      {(close) => (
        <>
          <SheetHead title={t("sh_choose_title")} onClose={close} />
          {!slots && !error && <p className="hint">{t("loading")}</p>}
          {slots && slots.length === 0 && <MascotTip tone="calm">{t("sh_no_slots")}</MascotTip>}
          {slots && slots.length > 0 && (
            <div className="slot-choices">
              {slots.map((s) => (
                <button key={s.id} className={`slot-choice${s.mine ? " mine" : ""}`} disabled={busy || s.mine} onClick={() => book(s.id)}>
                  <span className="row-title">{formatSlot(s.starts_at, s.ends_at)}</span>
                  <span className="hint small">
                    {s.mine ? t("sh_mine") : s.capacity > 1 ? k("sh_free", { n: String(s.free) }) : t("sh_private")}
                  </span>
                </button>
              ))}
            </div>
          )}
          {error && <p className="error">{error}</p>}
        </>
      )}
    </Sheet>
  );
}
