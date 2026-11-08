// Оренда (BOT_FLOWS §7, §9): список, нова оренда, запрошення, картка, умови, сповіщення.

import { useEffect, useState } from "react";

import {
  api,
  ApiFailure,
  type ContractState,
  type Invite,
  type InvitePreview,
  type Lease,
  type LeaseConditions,
  type LeaseSummary,
  type LeaseTerms,
  type NotifyPrefs,
  type Photo,
  type Property,
} from "../api/client";
import { botLink } from "../config";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import { AgreementBar, AgreementSheet } from "./AgreementBar";
import { ContractBlock } from "./Contract";
import { PaymentsBlock } from "./Payments";
import { PhotoSlider } from "./Photos";
import { Stepper, type Step } from "./Stepper";
import { confirm, openTelegramLink, type WebApp } from "../telegram";
import { Chip, errorCode, errorField, errorText, formatAddress, formatDate, ListRow, MascotTip, Screen } from "../ui";

const k = (key: string) => t(key as TextKey);

const STATUS_TONE: Record<Lease["status"], "neutral" | "ok" | "warn" | "info" | "bad"> = {
  draft: "neutral",
  awaiting_tenant: "warn",
  awaiting_signatures: "info",
  active: "ok",
  ending: "warn",
  ended: "neutral",
  cancelled: "bad",
};

export function LeaseStatusChip({ status }: { status: Lease["status"] }) {
  return <Chip tone={STATUS_TONE[status]}>{k(`ls_status_${status}`)}</Chip>;
}

function money(n: number): string {
  return n.toLocaleString("uk-UA");
}

function leaseAddress(l: LeaseSummary): string {
  return formatAddress({ street: l.street, house_number: l.house_number, apartment: l.apartment });
}

function inviteLink(token: string): string {
  return botLink(`inv_${token}`);
}

// ------------------------------------------------------------------ список

/** Меню «⋯» на рядку списку (окремою кнопкою: кнопка в кнопці — некоректний HTML). */
function RowMenu({ label, danger, onPick }: { label: string; danger?: boolean; onPick: () => void }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [open]);
  return (
    <>
      <button
        className="row-menu-btn"
        aria-label={t("ls_row_actions")}
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        <Icon name="more" size={22} />
      </button>
      {open && (
        <div className="menu-pop" role="menu">
          <button
            role="menuitem"
            className={danger ? "danger" : undefined}
            onClick={(e) => {
              e.stopPropagation();
              setOpen(false);
              onPick();
            }}
          >
            {label}
          </button>
        </div>
      )}
    </>
  );
}

export function LeaseList({
  onBack,
  onOpen,
  webApp,
}: {
  onBack?: () => void;
  onOpen: (id: string) => void;
  webApp: WebApp;
}) {
  const [items, setItems] = useState<LeaseSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.leases().then(setItems).catch((e: unknown) => setError(errorText(e)));
  }, []);

  return (
    <Screen onBack={onBack}>
      <h1>{t("ls_list_title")}</h1>
      {error && <p className="error">{error}</p>}
      {!items && !error && <div className="skeleton" />}
      {items && items.length === 0 && (
        <MascotTip>
          <p>{t("ls_list_empty")}</p>
        </MascotTip>
      )}
      {items?.map((l) => (
        <div key={l.id} className={["cancelled", "ended"].includes(l.status) ? "row-with-menu" : undefined}>
          <ListRow
            icon="key"
            title={leaseAddress(l)}
            sub={
              <>
                <LeaseStatusChip status={l.status} />
                <span>{k(`ls_role_${l.my_role}`)}</span>
              </>
            }
            right={l.unread > 0 && <span className="count">{l.unread}</span>}
            onClick={() => onOpen(l.id)}
          />
          {["cancelled", "ended"].includes(l.status) && (
            <RowMenu
              label={t("ls_delete")}
              danger
              onPick={async () => {
                const text = l.status === "ended" ? t("ls_hide_ended_confirm") : t("ls_delete_confirm");
                if (!(await confirm(webApp, text))) return;
                try {
                  await api.hideLease(l.id);
                  setItems((xs) => xs?.filter((x) => x.id !== l.id) ?? null);
                } catch (e) {
                  setError(errorText(e));
                }
              }}
            />
          )}
        </div>
      ))}
    </Screen>
  );
}

// -------------------------------------------------------- нова оренда / умови

type Form = {
  unitId: string | null;
  start: string;
  months: number;
  rent: string;
  dueDay: number;
  depositMode: "none" | "month" | "custom";
  deposit: string;
  payMethod: LeaseConditions["pay_method"];
  card: string;
  iban: string;
  jar: string;
  recipient: string;
  terms: LeaseTerms;
};

const DEFAULT_TERMS: LeaseTerms = {
  visits_notice_hours: 24,
  minor_repairs: "tenant",
  major_repairs: "landlord",
  pets_allowed: false,
  sublet_allowed: false,
  utilities_payer: "tenant_direct",
  residence_registration: false,
  early_termination_notice_days: 30,
  dispute_window_days: 3,
};

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function firstOfNextMonth(): string {
  const d = new Date();
  return isoDate(new Date(d.getFullYear(), d.getMonth() + 1, 1));
}

function monthsOf(l: Lease): number {
  return l.months_total;
}

function fromLease(l: Lease): Form {
  const dep = l.deposit_amount;
  return {
    unitId: null,
    start: l.start_date,
    months: monthsOf(l),
    rent: String(l.rent_amount),
    dueDay: l.rent_due_day,
    depositMode: dep == null || dep === 0 ? "none" : dep === l.rent_amount ? "month" : "custom",
    deposit: dep ? String(dep) : "",
    payMethod: l.pay.method,
    card: "",
    iban: "",
    jar: "",
    recipient: "",
    terms: l.terms,
  };
}

function Chips<T extends string | number | boolean>({
  value,
  options,
  label,
  onChange,
}: {
  value: T;
  options: T[];
  label: (v: T) => string;
  onChange: (v: T) => void;
}) {
  return (
    <div className="chips">
      {options.map((o) => (
        <button
          key={String(o)}
          type="button"
          className={`chip${value === o ? " active" : ""}`}
          aria-pressed={value === o}
          onClick={() => onChange(o)}
        >
          {label(o)}
        </button>
      ))}
    </div>
  );
}

/** Перемикач в один рядок для коротких варіантів (дні, місяці, так/ні). */
function Segmented<T extends string | number | boolean>({
  value,
  options,
  label,
  onChange,
  name,
}: {
  value: T;
  options: T[];
  label: (v: T) => string;
  onChange: (v: T) => void;
  name: string;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={name}>
      {options.map((o) => (
        <button
          key={String(o)}
          type="button"
          role="radio"
          aria-checked={value === o}
          className={value === o ? "active" : ""}
          onClick={() => onChange(o)}
        >
          {label(o)}
        </button>
      ))}
    </div>
  );
}

const MONTHS = [6, 11, 12, 24, 35];
const OPEN: Lease["status"][] = ["draft", "awaiting_tenant", "awaiting_signatures", "active", "ending"];

/** LSE-NEW / умови оренди: слайдер кроків (як створення об'єкта). Той самий для зміни умов і пропозицій орендаря. */
export function LeaseForm({
  property,
  lease,
  onBack,
  onCreated,
  onSaved,
  onOpenLease,
  propose = false,
}: {
  property?: Property;
  lease?: Lease;
  onBack: () => void;
  onCreated?: (lease: Lease, invite: Invite) => void;
  onSaved?: (lease: Lease) => void;
  /** Об'єкт уже зайнятий — відкрити наявну оренду. */
  onOpenLease?: (id: string) => void;
  /** Орендар пропонує зміни (FR-LSE-10): без реквізитів оплати. */
  propose?: boolean;
}) {
  const [f, setF] = useState<Form>(() =>
    lease
      ? fromLease(lease)
      : {
          unitId: null,
          start: firstOfNextMonth(),
          months: 11,
          rent: "",
          dueDay: 5,
          depositMode: "month",
          deposit: "",
          payMethod: "card",
          card: "",
          iban: "",
          jar: "",
          recipient: "",
          terms: DEFAULT_TERMS,
        },
  );
  // Зміна умов відкривається з підсумку: олівцем — у потрібний розділ.
  const [at, setAt] = useState(lease ? (propose ? 4 : 5) : 0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Незавершені оренди цього об'єкта: другу на те саме не створити (сервер відповість 409).
  const [taken, setTaken] = useState<LeaseSummary[]>([]);
  const set = (patch: Partial<Form>) => setF((s) => ({ ...s, ...patch }));
  const term = (patch: Partial<LeaseTerms>) => setF((s) => ({ ...s, terms: { ...s.terms, ...patch } }));

  useEffect(() => {
    if (!property || lease) return;
    api
      .leases()
      .then((all) => setTaken(all.filter((l) => l.property_id === property.id && OPEN.includes(l.status))))
      .catch(() => setTaken([]));
  }, [property, lease]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [at]);

  const go = (i: number) => {
    setError(null);
    setAt(i);
  };

  // Кімнати поки не здаємо (ADR 0013): будь-яка незавершена оренда займає об'єкт.
  const blocking = taken[0];

  function body(): LeaseConditions {
    const rent = Number(f.rent);
    const deposit = f.depositMode === "none" ? null : f.depositMode === "month" ? rent : Number(f.deposit) || 0;
    return {
      start_date: f.start,
      months: f.months,
      rent_amount: rent,
      rent_due_day: f.dueDay,
      deposit_amount: deposit,
      pay_method: f.payMethod,
      pay_details:
        f.payMethod === "cash"
          ? null
          : { card: f.card || null, iban: f.iban || null, jar_url: f.jar || null, recipient_name: f.recipient || null },
      terms: f.terms,
    };
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      if (lease && propose) {
        const b = body();
        onSaved?.(
          await api.proposeChanges(lease.id, {
            start_date: b.start_date,
            months: b.months,
            rent_amount: b.rent_amount,
            rent_due_day: b.rent_due_day,
            deposit_amount: b.deposit_amount,
            terms: b.terms,
          }),
        );
      } else if (lease) {
        onSaved?.(await api.updateConditions(lease.id, body()));
      } else if (property) {
        const r = await api.createLease({ ...body(), property_id: property.id, unit_id: f.unitId });
        onCreated?.(r.lease, r.invite);
      }
    } catch (e) {
      setError(leaseError(e));
      // Помилка в полі — повертаємо на крок, де це поле.
      const field = errorField(e);
      const where = steps.findIndex((s) => s.fields?.includes(field ?? ""));
      if (where >= 0) setAt(where);
      setBusy(false);
    }
  }

  const payLine =
    f.payMethod === "cash"
      ? t("ls_pay_cash")
      : `${k(`ls_pay_${f.payMethod}`)}${f.recipient ? ` · ${f.recipient}` : ""}`;
  const rentOk = Number(f.rent) > 0;
  const depositOk = f.depositMode !== "custom" || Number(f.deposit) > 0;
  // Сервер вимагає реквізити й отримувача щоразу (зберігає їх лише зашифрованими) — перевіряємо до відправки.
  const payOk =
    f.payMethod === "cash" ||
    (f.recipient.trim().length >= 3 &&
      ((f.payMethod === "card" && digitsOnly(f.card).length >= 16) ||
        (f.payMethod === "iban" && f.iban.replace(/\s/g, "").length >= 29) ||
        (f.payMethod === "jar" && f.jar.trim().length > 10)));
  const next = (i: number, disabled = false) => ({ label: t("next"), disabled, onClick: () => go(i + 1) });

  type LeaseStep = Step & { fields?: string[] };
  const draft: LeaseStep[] = [
    {
      key: "dates",
      title: t("ls_step_dates_title"),
      text: lease ? undefined : t("ls_new_hint"),
      fields: ["unit_id", "start_date", "months", "end_date"],
      content: (
        <>
          {blocking && (
            <section className="card notice">
              <p>
                {t("ls_busy_property", { status: k(`ls_status_${blocking.status}`).toLowerCase() })}
              </p>
              {onOpenLease && (
                <button className="button ghost small" onClick={() => onOpenLease(blocking.id)}>
                  <Icon name="key" size={18} />
                  {t("ls_open_existing")}
                </button>
              )}
            </section>
          )}
          {/* Зайнятий об'єкт — заповнювати нічого. */}
          {!blocking && (
          <section className="card">
            {property && <p className="row-title">{formatAddress(property)}</p>}
            <label className="field">
              {t("ls_start")}
              <input className="input" type="date" value={f.start} onChange={(e) => set({ start: e.target.value })} />
            </label>
            <div className="chips">
              <button type="button" className={`chip${f.start === firstOfNextMonth() ? " active" : ""}`} onClick={() => set({ start: firstOfNextMonth() })}>
                {t("ls_start_next_month")}
              </button>
              <button type="button" className={`chip${f.start === isoDate(new Date()) ? " active" : ""}`} onClick={() => set({ start: isoDate(new Date()) })}>
                {t("ls_start_today")}
              </button>
            </div>
            <p className="field">{t("ls_months")}</p>
            <Segmented<number> name={t("ls_months")} value={f.months} options={MONTHS} label={String} onChange={(n) => set({ months: n })} />
            {!MONTHS.includes(f.months) && <p className="hint small">{t("ls_months_custom", { n: String(f.months) })}</p>}
            <label className="field">
              {t("ls_months_other")}
              <input
                className="input"
                inputMode="numeric"
                value={String(f.months)}
                onChange={(e) => set({ months: Math.max(1, Math.min(35, Number(e.target.value.replace(/\D/g, "")) || 1)) })}
              />
            </label>
            <p className="hint small">{t("ls_months_hint")}</p>
          </section>
          )}
        </>
      ),
      action: next(0, !!blocking || !f.start),
    },
    {
      key: "money",
      title: t("ls_step_money_title"),
      fields: ["rent_amount", "rent_due_day", "deposit_amount"],
      content: (
        <section className="card">
          <label className="field">
            {t("ls_rent")}
            <input
              className="input"
              inputMode="numeric"
              value={f.rent}
              placeholder="15000"
              onChange={(e) => set({ rent: e.target.value.replace(/\D/g, "") })}
            />
          </label>
          <p className="field">{t("ls_due_day")}</p>
          <Segmented<number> name={t("ls_due_day")} value={f.dueDay} options={[1, 5, 10, 15, 20, 25]} label={String} onChange={(n) => set({ dueDay: n })} />
          <p className="field">{t("ls_deposit")}</p>
          <Segmented<Form["depositMode"]>
            name={t("ls_deposit")}
            value={f.depositMode}
            options={["none", "month", "custom"]}
            label={(v) => k(`ls_deposit_${v}`)}
            onChange={(v) => set({ depositMode: v })}
          />
          {f.depositMode === "custom" && (
            <label className="field">
              {t("ls_deposit_sum")}
              <input className="input" inputMode="numeric" value={f.deposit} onChange={(e) => set({ deposit: e.target.value.replace(/\D/g, "") })} />
            </label>
          )}
        </section>
      ),
      action: next(1, !rentOk || !depositOk),
    },
    ...(propose
      ? []
      : [
          {
            key: "pay",
            title: t("ls_sec_pay"),
            text: t("ls_pay_hint"),
            fields: ["card", "iban", "jar_url", "recipient_name", "pay_details"],
            content: (
              <section className="card">
                <Segmented<Form["payMethod"]>
                  name={t("ls_sec_pay")}
                  value={f.payMethod}
                  options={["card", "iban", "jar", "cash"]}
                  label={(v) => k(`ls_pay_${v}_short`)}
                  onChange={(v) => set({ payMethod: v })}
                />
                {f.payMethod === "card" && (
                  <label className="field">
                    {t("ls_card")}
                    <input className="input" inputMode="numeric" autoComplete="off" value={f.card} placeholder="0000 0000 0000 0000" onChange={(e) => set({ card: e.target.value })} />
                  </label>
                )}
                {f.payMethod === "iban" && (
                  <label className="field">
                    {t("ls_iban")}
                    <input className="input" autoComplete="off" value={f.iban} placeholder="UA…" onChange={(e) => set({ iban: e.target.value })} />
                  </label>
                )}
                {f.payMethod === "jar" && (
                  <label className="field">
                    {t("ls_jar")}
                    <input className="input" value={f.jar} placeholder="https://send.monobank.ua/jar/…" onChange={(e) => set({ jar: e.target.value })} />
                  </label>
                )}
                {f.payMethod !== "cash" ? (
                  <label className="field">
                    {t("ls_recipient")}
                    <input className="input" value={f.recipient} onChange={(e) => set({ recipient: e.target.value })} />
                  </label>
                ) : (
                  <p className="hint small">{t("ls_pay_cash_hint")}</p>
                )}
                {lease && f.payMethod !== "cash" && <p className="hint small">{t("ls_pay_keep_hint")}</p>}
              </section>
            ),
          } as LeaseStep,
        ]),
    {
      key: "home",
      title: t("ls_step_home_title"),
      fields: ["visits_notice_hours", "minor_repairs", "major_repairs", "utilities_payer"],
      content: (
        <section className="card">
          <p className="field">{t("ls_visits")}</p>
          <Segmented<number>
            name={t("ls_visits")}
            value={f.terms.visits_notice_hours}
            options={[12, 24, 48]}
            label={(n) => t("ls_hours_n", { n: String(n) })}
            onChange={(n) => term({ visits_notice_hours: n })}
          />
          <p className="field">{t("ls_minor")}</p>
          <Segmented<LeaseTerms["minor_repairs"]>
            name={t("ls_minor")}
            value={f.terms.minor_repairs}
            options={["tenant", "landlord"]}
            label={(v) => k(`ls_side_${v}`)}
            onChange={(v) => term({ minor_repairs: v })}
          />
          <p className="field">{t("ls_major")}</p>
          <Segmented<LeaseTerms["major_repairs"]>
            name={t("ls_major")}
            value={f.terms.major_repairs}
            options={["landlord", "tenant"]}
            label={(v) => k(`ls_side_${v}`)}
            onChange={(v) => term({ major_repairs: v })}
          />
          <div className="field">
            {t("ls_utilities")}
            <Chips<LeaseTerms["utilities_payer"]>
              value={f.terms.utilities_payer}
              options={["tenant_direct", "landlord", "included"]}
              label={(v) => k(`ls_util_${v}`)}
              onChange={(v) => term({ utilities_payer: v })}
            />
          </div>
        </section>
      ),
    },
    {
      key: "rules",
      title: t("ls_step_rules_title"),
      fields: ["pets_allowed", "residence_registration", "sublet_allowed", "early_termination_notice_days", "dispute_window_days"],
      content: (
        <section className="card">
          <p className="field">{t("ls_pets")}</p>
          <Segmented<boolean> name={t("ls_pets")} value={f.terms.pets_allowed} options={[true, false]} label={(b) => (b ? t("pf_yes") : t("pf_no"))} onChange={(b) => term({ pets_allowed: b })} />
          <p className="field">{t("ls_registration")}</p>
          <Segmented<boolean>
            name={t("ls_registration")}
            value={f.terms.residence_registration}
            options={[true, false]}
            label={(b) => (b ? t("pf_yes") : t("pf_no"))}
            onChange={(b) => term({ residence_registration: b })}
          />
          <p className="field">{t("ls_sublet")}</p>
          <Segmented<boolean>
            name={t("ls_sublet")}
            value={f.terms.sublet_allowed}
            options={[false, true]}
            label={(b) => (b ? t("ls_sublet_yes") : t("ls_sublet_no"))}
            onChange={(b) => term({ sublet_allowed: b })}
          />
          <p className="field">{t("ls_early")}</p>
          <Segmented<number>
            name={t("ls_early")}
            value={f.terms.early_termination_notice_days}
            options={[14, 30, 60]}
            label={(n) => t("ls_days_n", { n: String(n) })}
            onChange={(n) => term({ early_termination_notice_days: n })}
          />
          <p className="field">{t("ls_dispute")}</p>
          <Segmented<LeaseTerms["dispute_window_days"]>
            name={t("ls_dispute")}
            value={f.terms.dispute_window_days}
            options={[1, 3, 5, 7]}
            label={(n) => t("ls_days_short", { n: String(n) })}
            onChange={(n) => term({ dispute_window_days: n })}
          />
          <p className="hint small">{t("ls_dispute_hint")}</p>
        </section>
      ),
    },
    {
      key: "confirm",
      title: t(lease ? "ls_step_confirm_edit_title" : "ls_step_confirm_title"),
      content: (
        <section className="card">
          {property && (
            <div className="summary-head">
              <span className="row-icon">
                <Icon name={property.kind === "house" ? "home" : "building"} />
              </span>
              <p className="row-title">{formatAddress(property)}</p>
            </div>
          )}
          <dl className="summary-list">
            <SummaryRow label={t("ls_sec_dates")} value={t("ls_summary_dates", { from: formatDate(f.start), n: String(f.months) })} onEdit={() => go(0)} />
            <SummaryRow
              label={t("ls_sec_money")}
              value={`${t("ls_rent_line", { rent: f.rent || "0", day: String(f.dueDay) })}${
                f.depositMode === "none" ? "" : ` · ${t("ls_deposit_line", { sum: String(body().deposit_amount ?? 0) })}`
              }`}
              onEdit={() => go(1)}
            />
            {!propose && <SummaryRow label={t("ls_sec_pay")} value={payLine} onEdit={() => go(2)} />}
            <SummaryRow label={t("ls_sec_terms")} value={t("ls_summary_terms")} onEdit={() => go(propose ? 2 : 3)} />
          </dl>
        </section>
      ),
      action: {
        label: busy ? t("saving") : lease ? t(propose ? "ls_propose_send" : "ls_save") : t("ls_create"),
        final: true,
        icon: lease ? "check" : "send",
        disabled: busy || !rentOk || !depositOk || (!propose && !payOk) || !!blocking,
        onClick: () => void submit(),
      },
    },
  ];
  // Реквізити — окремий крок лише в «новій оренді» й зміні умов; кнопку «Далі» додаємо там, де її немає.
  const steps = draft.map((s, i) => (s.action || i === draft.length - 1 ? s : { ...s, action: next(i, s.key === "pay" && !payOk) }));

  const title = propose ? t("ls_propose_title") : lease ? t("ls_edit_title") : t("ls_new_title");
  return <Stepper steps={steps} at={at} onGo={go} onExit={onBack} title={title} error={error} open={!!lease} />;
}

function SummaryRow({ label, value, onEdit }: { label: string; value: string; onEdit: () => void }) {
  return (
    <div className="summary-row">
      <dt>{label}</dt>
      <dd>{value}</dd>
      <button type="button" className="icon-button" aria-label={t("edit")} onClick={onEdit}>
        <Icon name="edit" size={18} />
      </button>
    </div>
  );
}

function digitsOnly(raw: string): string {
  return raw.replace(/\D/g, "");
}

function leaseError(e: unknown): string {
  const field = errorField(e);
  if (errorCode(e) === "conflict" && field === "unit_id") return t("ls_err_unit_busy");
  return field ? t("prp_bad_field", { field: fieldLabel(field) }) : errorText(e);
}

function fieldLabel(field: string): string {
  const map: Record<string, TextKey> = {
    card: "ls_card",
    iban: "ls_iban",
    jar_url: "ls_jar",
    recipient_name: "ls_recipient",
    rent_amount: "ls_rent",
    rent_due_day: "ls_due_day",
    deposit_amount: "ls_deposit",
    end_date: "ls_months",
    months: "ls_months",
    unit_id: "ls_unit",
  };
  return map[field] ? t(map[field]) : field;
}

// -------------------------------------------------------------- запрошення

export function InviteShare({
  invite,
  webApp,
  onDone,
}: {
  invite: Invite;
  webApp: WebApp;
  onDone: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const link = inviteLink(invite.token);

  async function copy() {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Screen onBack={onDone} title={t("ls_invite_title")}>
      <MascotTip>
        <p>{invite.role === "responsible" ? t("ls_invite_tip") : t("ls_invite_tip_co")}</p>
      </MascotTip>
      {link ? (
        <section className="card">
          <p className="invite-link">{link}</p>
          <p className="hint small">{t("ls_invite_until", { date: formatDate(invite.expires_at) })}</p>
          <button className="button" onClick={() => openTelegramLink(webApp, `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(t("ls_share_text"))}`)}>
            <Icon name="send" size={20} />
            {t("ls_share")}
          </button>
          <button className="button secondary" onClick={copy}>
            {copied ? t("ls_copied") : t("ls_copy")}
          </button>
        </section>
      ) : (
        <p className="error">{t("ls_no_bot")}</p>
      )}
      <button className="button ghost" onClick={onDone}>
        {t("ls_open_lease")}
      </button>
    </Screen>
  );
}

export function InviteAccept({
  token,
  onBack,
  onJoined,
}: {
  token: string;
  onBack: () => void;
  onJoined: (lease: Lease) => void;
}) {
  const [p, setP] = useState<InvitePreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.previewInvite(token).then(setP).catch((e: unknown) => setError(errorText(e)));
  }, [token]);

  async function join() {
    setBusy(true);
    setError(null);
    try {
      onJoined(await api.acceptInvite(token));
    } catch (e) {
      const reason = e instanceof ApiFailure ? e.reason : undefined;
      const code = errorCode(e);
      setError(
        code === "lease_limit"
          ? t("ls_invite_lease_limit")
          : code === "invite_invalid"
            ? k(`ls_invite_${reason ?? "not_found"}`)
            : errorText(e),
      );
      setBusy(false);
    }
  }

  return (
    <Screen onBack={onBack} title={t("ls_accept_title")}>
      {!p && !error && <div className="skeleton" style={{ height: 160 }} />}
      {p && !p.valid && <p className="error">{k(`ls_invite_${p.reason}`)}</p>}
      {p && p.valid && (
        <>
          <MascotTip>
            <p>
              {p.role === "responsible"
                ? t("ls_accept_responsible", { name: p.landlord_name ?? "" })
                : t("ls_accept_co", { name: p.landlord_name ?? "" })}
            </p>
          </MascotTip>
          <section className="card">
            <h2>{formatAddress({ street: p.street ?? "", house_number: p.house_number ?? "", apartment: p.apartment })}</h2>
            <p>{t("ls_period", { from: formatDate(p.start_date), to: formatDate(p.end_date) })}</p>
            {p.rent_amount != null && <p className="hint">{money(p.rent_amount)} ₴</p>}
          </section>
          <button className="button" disabled={busy} onClick={join}>
            {t("ls_join")}
          </button>
        </>
      )}
      {error && <p className="error">{error}</p>}
    </Screen>
  );
}

// -------------------------------------------------------------- картка

function termsLines(terms: LeaseTerms): string[] {
  return [
    t("ls_t_visits", { n: String(terms.visits_notice_hours) }),
    t("ls_t_repairs", { minor: k(`ls_side_${terms.minor_repairs}`), major: k(`ls_side_${terms.major_repairs}`) }),
    terms.pets_allowed ? t("ls_t_pets_yes") : t("ls_t_pets_no"),
    t("ls_t_util", { who: k(`ls_util_${terms.utilities_payer}`) }),
    terms.residence_registration ? t("ls_t_reg_yes") : t("ls_t_reg_no"),
    t("ls_t_early", { n: String(terms.early_termination_notice_days) }),
    terms.sublet_allowed ? t("ls_t_sublet_yes") : t("ls_t_sublet_no"),
  ];
}

const TERMINATION_REASONS = ["ls_term_r_moving", "ls_term_r_landlord", "ls_term_r_breach"] as const;

/** FR-LSE-7: розірвання — форма для активної оренди й стан «завершується». */
function TerminationBlock({
  l,
  webApp,
  busy,
  run,
}: {
  l: Lease;
  webApp: WebApp;
  busy: boolean;
  run: (action: () => Promise<Lease | void>) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(l.termination_earliest ?? l.end_date);
  const [reason, setReason] = useState("");
  const mine = l.termination && l.termination.by === l.my_role;

  if (l.status === "ending" && l.termination) {
    return (
      <section className="card accent">
        <h2>{t("ls_term_ending_title", { date: formatDate(l.termination.date) })}</h2>
        <p className="hint left">
          {k(`ls_term_by_${l.termination.by}`)}: «{l.termination.reason}»
        </p>
        <p className="hint small">{t("ls_term_ending_tip")}</p>
        {mine && (
          <button
            className="button secondary"
            disabled={busy}
            onClick={async () => {
              if (!(await confirm(webApp, t("ls_term_withdraw_confirm")))) return;
              await run(() => api.withdrawTermination(l.id));
            }}
          >
            {t("ls_term_withdraw")}
          </button>
        )}
      </section>
    );
  }
  if (l.status !== "active" || !l.can_terminate) return null;
  if (!open) {
    return (
      <section className="card">
        <h2>{t("ls_term_title")}</h2>
        <p className="hint">
          {t("ls_term_hint", { end: formatDate(l.end_date), n: String(l.terms.early_termination_notice_days) })}
        </p>
        <button className="button ghost" onClick={() => setOpen(true)}>
          {t("ls_term_start")}
        </button>
      </section>
    );
  }
  return (
    <section className="card accent">
      <h2>{t("ls_term_title")}</h2>
      <label className="field">
        {t("ls_term_date")}
        <input
          className="input"
          type="date"
          value={date}
          min={l.termination_earliest ?? undefined}
          max={l.end_date}
          onChange={(e) => setDate(e.target.value)}
        />
      </label>
      <p className="hint small">
        {t("ls_term_date_hint", { n: String(l.terms.early_termination_notice_days), date: formatDate(l.termination_earliest ?? l.end_date) })}
      </p>
      <div className="field">
        {t("ls_term_reason")}
        <div className="chips">
          {TERMINATION_REASONS.map((key) => (
            <button key={key} className={`chip${reason === t(key) ? " active" : ""}`} onClick={() => setReason(t(key))}>
              {t(key)}
            </button>
          ))}
        </div>
        <textarea
          className="input"
          rows={3}
          maxLength={500}
          value={reason}
          placeholder={t("ls_term_reason_placeholder")}
          onChange={(e) => setReason(e.target.value)}
        />
      </div>
      <button
        className="button"
        disabled={busy || reason.trim().length < 3 || !date}
        onClick={async () => {
          if (!(await confirm(webApp, t("ls_term_confirm", { date: formatDate(date) })))) return;
          await run(async () => {
            const r = await api.requestTermination(l.id, date, reason);
            setOpen(false);
            return r;
          });
        }}
      >
        {t("ls_term_send")}
      </button>
      <button className="link" onClick={() => setOpen(false)}>
        {t("cancel")}
      </button>
    </section>
  );
}

function hasResponsibleMember(l: Lease): boolean {
  return l.members.some((m) => m.role === "responsible");
}

export function LeaseCard({
  id,
  webApp,
  onBack,
  onChat,
  onEdit,
  onNotify,
  onInvite,
  onPropose,
}: {
  id: string;
  webApp: WebApp;
  onBack: () => void;
  onChat: () => void;
  onEdit: (lease: Lease) => void;
  onNotify: () => void;
  onInvite: (invite: Invite) => void;
  onPropose: (lease: Lease) => void;
}) {
  const [l, setL] = useState<Lease | null>(null);
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [contract, setContract] = useState<ContractState | null>(null);
  const [sheet, setSheet] = useState(false);
  const [changes, setChanges] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.lease(id).then(setL).catch((e: unknown) => setError(errorText(e)));
    api
      .leasePhotos(id)
      .then(setPhotos)
      .catch(() => setPhotos([]));
  }, [id]);

  async function run(action: () => Promise<Lease | void>) {
    setBusy(true);
    setError(null);
    try {
      const r = await action();
      if (r) setL(r);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (!l) {
    return (
      <Screen onBack={onBack}>
        {error ? <p className="error">{error}</p> : <div className="skeleton" style={{ height: 200 }} />}
      </Screen>
    );
  }

  const open = !["cancelled", "ended"].includes(l.status);
  const iAmResponsible = l.my_role === "responsible";
  const needsMyAgreement =
    iAmResponsible && l.status === "awaiting_tenant" && l.agreement.tenant !== "agreed" && !l.proposal;
  const proposalForMe = l.my_role === "landlord" && !!l.proposal && l.status === "awaiting_tenant";
  const landlordMustAgree =
    l.my_role === "landlord" && l.status === "awaiting_tenant" && !l.proposal && l.agreement.landlord !== "agreed";
  const showBar = ["awaiting_tenant", "awaiting_signatures"].includes(l.status) && hasResponsibleMember(l);
  const hasResponsible = l.members.some((m) => m.role === "responsible");
  const payText =
    l.pay.method === "cash" ? k("ls_pay_cash") : `${k(`ls_pay_${l.pay.method}`)} ${l.pay.masked ?? ""} · ${l.pay.recipient ?? ""}`;

  return (
    <Screen onBack={onBack}>
      <PhotoSlider photos={photos} source={{ kind: "lease", id: l.id }} compact />
      <div className="stat" style={{ marginTop: 8 }}>
        <LeaseStatusChip status={l.status} />
        <span className="hint small">{k(`ls_role_${l.my_role}`)}</span>
      </div>
      <h1 className="left">{leaseAddress(l)}</h1>
      <p className="hint left">
        {t("ls_period", { from: formatDate(l.start_date), to: formatDate(l.end_date) })}
        {open && ` · ${t("ls_left", { n: String(l.months_left) })}`}
      </p>

      {needsMyAgreement && (
        <section className="card accent">
          <h2>{t("ls_review_title")}</h2>
          <MascotTip tone="calm">
            <p>{t("ls_review_tip")}</p>
          </MascotTip>
          {changes === null ? (
            <>
              <button className="button" disabled={busy} onClick={() => run(() => api.acceptTerms(l.id))}>
                <Icon name="check" size={20} />
                {t("ls_accept_terms")}
              </button>
              <div className="buttons">
                <button className="button secondary" onClick={() => onPropose(l)}>
                  {t("ls_propose_changes")}
                </button>
                <button
                  className="button ghost"
                  disabled={busy}
                  onClick={async () => {
                    if (!(await confirm(webApp, t("ls_decline_confirm")))) return;
                    await run(async () => {
                      await api.leaveLease(l.id);
                      onBack();
                    });
                  }}
                >
                  {t("ls_decline")}
                </button>
              </div>
            </>
          ) : (
            <>
              <textarea
                className="input"
                rows={4}
                maxLength={2000}
                value={changes}
                placeholder={t("ls_changes_placeholder")}
                onChange={(e) => setChanges(e.target.value)}
              />
              <button
                className="button"
                disabled={busy || !changes.trim()}
                onClick={() =>
                  run(async () => {
                    const r = await api.requestChanges(l.id, changes);
                    setChanges(null);
                    return r;
                  })
                }
              >
                {t("ls_send_request")}
              </button>
              <button className="link" onClick={() => setChanges(null)}>
                {t("cancel")}
              </button>
            </>
          )}
        </section>
      )}

      {proposalForMe && l.proposal && (
        <section className="card accent">
          <h2>{t("ls_proposal_title")}</h2>
          <p className="hint">{t("ls_proposal_tip")}</p>
          <button className="link" onClick={() => setSheet(true)}>
            {t("ls_proposal_view")}
          </button>
          <button className="button" disabled={busy} onClick={() => run(() => api.respondProposal(l.id, true))}>
            <Icon name="check" size={20} />
            {t("ls_proposal_accept")}
          </button>
          <button
            className="button ghost"
            disabled={busy}
            onClick={async () => {
              if (!(await confirm(webApp, t("ls_proposal_decline_confirm")))) return;
              await run(() => api.respondProposal(l.id, false));
            }}
          >
            {t("ls_proposal_decline")}
          </button>
        </section>
      )}
      {landlordMustAgree && (
        <section className="card accent">
          <h2>{t("ls_landlord_agree_title")}</h2>
          <p className="hint">{t("ls_landlord_agree_tip")}</p>
          <button className="button" disabled={busy} onClick={() => run(() => api.acceptTerms(l.id))}>
            <Icon name="check" size={20} />
            {t("ls_accept_terms")}
          </button>
          {l.can_edit && (
            <button className="button secondary" onClick={() => onEdit(l)}>
              <Icon name="edit" size={20} />
              {t("ls_edit")}
            </button>
          )}
        </section>
      )}
      {l.status === "awaiting_tenant" && l.my_role === "landlord" && !proposalForMe && !landlordMustAgree && (
        <MascotTip>
          <p>{hasResponsible ? t("ls_landlord_review_wait") : t("ls_wait_tenant_tip")}</p>
        </MascotTip>
      )}
      {iAmResponsible && l.status === "awaiting_tenant" && (l.proposal || l.agreement.tenant === "agreed") && (
        <MascotTip>
          <p>{l.proposal ? t("ls_proposal_sent_tip") : t("ls_tenant_agreed_wait")}</p>
        </MascotTip>
      )}
      {(l.status === "awaiting_signatures" || l.status === "active") && l.my_role !== "co_tenant" && (
        <ContractBlock
          key={`${l.id}:${l.status}`}
          leaseId={l.id}
          webApp={webApp}
          onState={setContract}
          onActivated={() => {
            api.lease(l.id).then(setL).catch(() => undefined);
          }}
        />
      )}
      {l.status === "awaiting_signatures" && l.my_role === "co_tenant" && (
        <MascotTip>
          <p>{t("ls_wait_contract_tip")}</p>
        </MascotTip>
      )}

      <div className="quick">
        <button className="quick-item" onClick={onChat}>
          <span className="row-icon">
            <Icon name="send" />
          </span>
          <span className="quick-title">{l.unread ? t("ls_chat_unread", { n: String(l.unread) }) : t("ls_chat")}</span>
        </button>
        <button className="quick-item" onClick={onNotify}>
          <span className="row-icon">
            <Icon name="alert" />
          </span>
          <span className="quick-title">{t("ls_notify")}</span>
        </button>
      </div>

      <section className="card">
        <h2>{t("ls_sec_money")}</h2>
        <p>{t("ls_rent_line", { rent: money(l.rent_amount), day: String(l.rent_due_day) })}</p>
        {l.deposit_amount ? <p className="hint">{t("ls_deposit_line", { sum: money(l.deposit_amount) })}</p> : null}
        <p className="hint">{t("ls_pay_line", { method: payText })}</p>
      </section>

      <section className="card">
        <h2>{t("ls_terms_title")}</h2>
        <ul className="plain">
          {termsLines(l.terms).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        {l.can_edit && (
          <button className="button secondary" onClick={() => onEdit(l)}>
            <Icon name="edit" size={20} />
            {t("ls_edit")}
          </button>
        )}
      </section>

      <section className="card">
        <h2>{t("ls_members")}</h2>
        {l.members.map((m) => (
          <div key={m.user_id} className="unit">
            <span>{m.display_name}</span>
            <span className="hint small">{k(`ls_member_${m.role}`)}</span>
          </div>
        ))}
        {l.pending_invites.map((i) => (
          <p key={i.role} className="hint small">
            {t("ls_pending_invite", { role: k(`ls_member_${i.role}`), date: formatDate(i.expires_at) })}
          </p>
        ))}
        {l.my_role === "landlord" && open && !hasResponsible && (
          <button className="button secondary" disabled={busy} onClick={() => run(async () => onInvite(await api.newInvite(l.id, "responsible")))}>
            {t("ls_invite_again")}
          </button>
        )}
        {l.can_invite_co_tenant && hasResponsible && (
          <button className="button secondary" disabled={busy} onClick={() => run(async () => onInvite(await api.newInvite(l.id, "co_tenant")))}>
            <Icon name="plus" size={20} />
            {t("ls_invite_co")}
          </button>
        )}
      </section>

      {error && <p className="error">{error}</p>}

      {l.my_role === "landlord" && l.can_edit && (
        <button
          className="link danger"
          disabled={busy}
          onClick={async () => {
            if (!(await confirm(webApp, t("ls_cancel_confirm")))) return;
            await run(() => api.cancelLease(l.id));
          }}
        >
          {t("ls_cancel")}
        </button>
      )}
      {["active", "ending", "ended"].includes(l.status) && (
        <PaymentsBlock leaseId={l.id} address={leaseAddress(l)} webApp={webApp} />
      )}
      {(l.status === "active" || l.status === "ending") && l.my_role !== "co_tenant" && (
        <TerminationBlock l={l} webApp={webApp} busy={busy} run={run} />
      )}
      {l.status === "ended" && (
        <MascotTip tone="calm">
          <p>{t("ls_ended_tip")}</p>
        </MascotTip>
      )}
      {showBar && (
        <>
          <div className="agreement-spacer" />
          <AgreementBar lease={l} contract={contract} onOpen={() => setSheet(true)} />
        </>
      )}
      {sheet && <AgreementSheet lease={l} contract={contract} termsLines={termsLines} onClose={() => setSheet(false)} />}
      {(l.status === "cancelled" || l.status === "ended") && (
        <button
          className="link danger"
          disabled={busy}
          onClick={async () => {
            if (!(await confirm(webApp, t("ls_delete_confirm")))) return;
            await run(async () => {
              await api.hideLease(l.id);
              onBack();
            });
          }}
        >
          {t("ls_delete")}
        </button>
      )}
      {l.my_role === "co_tenant" && open && (
        <button
          className="link danger"
          disabled={busy}
          onClick={async () => {
            if (!(await confirm(webApp, t("ls_leave_confirm")))) return;
            await run(async () => {
              await api.leaveLease(l.id);
              onBack();
            });
          }}
        >
          {t("ls_leave")}
        </button>
      )}
    </Screen>
  );
}

// --------------------------------------------------------- сповіщення

export function LeaseNotifications({ id, onBack }: { id: string; onBack: () => void }) {
  const [p, setP] = useState<NotifyPrefs | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.notifyPrefs(id).then(setP).catch((e: unknown) => setError(errorText(e)));
  }, [id]);

  if (!p) {
    return <Screen onBack={onBack}>{error ? <p className="error">{error}</p> : <div className="skeleton" />}</Screen>;
  }
  const patch = (x: Partial<NotifyPrefs>) => {
    setP({ ...p, ...x });
    setSaved(false);
  };
  const hours = Array.from({ length: 24 }, (_, i) => i);

  return (
    <Screen onBack={onBack} title={t("nt_title")}>
      <MascotTip>
        <p>{t("nt_tip")}</p>
      </MascotTip>
      <section className="card">
        <div className="field">
          {t("nt_chat")}
          <Chips<boolean> value={p.chat_message ?? true} options={[true, false]} label={(b) => (b ? t("pf_yes") : t("pf_no"))} onChange={(b) => patch({ chat_message: b })} />
        </div>
        <div className="field">
          {t("nt_lease")}
          <Chips<boolean> value={p.lease_update ?? true} options={[true, false]} label={(b) => (b ? t("pf_yes") : t("pf_no"))} onChange={(b) => patch({ lease_update: b })} />
        </div>
        <div className="field">
          {t("nt_quiet")}
          <div className="pair">
            <label className="field">
              {t("nt_from")}
              <select className="input" value={p.quiet_from ?? 22} onChange={(e) => patch({ quiet_from: Number(e.target.value) })}>
                {hours.map((h) => (
                  <option key={h} value={h}>{`${String(h).padStart(2, "0")}:00`}</option>
                ))}
              </select>
            </label>
            <label className="field">
              {t("nt_to")}
              <select className="input" value={p.quiet_to ?? 8} onChange={(e) => patch({ quiet_to: Number(e.target.value) })}>
                {hours.map((h) => (
                  <option key={h} value={h}>{`${String(h).padStart(2, "0")}:00`}</option>
                ))}
              </select>
            </label>
          </div>
        </div>
      </section>
      {error && <p className="error">{error}</p>}
      {saved && <p className="ok">{t("prof_saved")}</p>}
      <button
        className="button"
        onClick={() =>
          api
            .setNotifyPrefs(id, p)
            .then((r) => {
              setP(r);
              setSaved(true);
            })
            .catch((e: unknown) => setError(errorText(e)))
        }
      >
        {t("nt_save")}
      </button>
    </Screen>
  );
}
