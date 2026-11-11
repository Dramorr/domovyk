// Оплати в картці оренди (M5, BOT_FLOWS §10). Домовик гроші не проводить:
// показуємо, скільки й куди платити, а людина платить у своєму банку й позначає оплату.

import { useCallback, useEffect, useState } from "react";

import { api, ApiFailure, type Charge, type PayInfo, type PaymentsOverview, type PaymentItem } from "../api/client";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import { confirm, openLink, type WebApp } from "../telegram";
import { Chip, errorText, formatDate, formatDateTime, Sheet } from "../ui";
import { PdfViewer } from "./PdfViewer";

const k = (key: string) => t(key as TextKey);

function money(v: string | number): string {
  const n = Number(v);
  return `${n.toLocaleString("uk-UA", { maximumFractionDigits: 2 })} ₴`;
}

function monthLabel(iso: string): string {
  const label = new Date(iso).toLocaleDateString("uk-UA", { month: "long", year: "numeric", timeZone: "UTC" });
  return label.replace(/\s*р\.$/, "");
}

function periodLabel(c: Pick<Charge, "kind" | "period_month">): string {
  if (c.kind === "deposit") return t("pay_deposit");
  if (c.kind === "utilities") return t("pay_utilities", { period: monthLabel(c.period_month) });
  return monthLabel(c.period_month);
}

function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** FR-UTL-5: власник нараховує комуналку, яку орендар платить йому. */
function UtilitySheet({
  leaseId,
  onClose,
  onDone,
}: {
  leaseId: string;
  onClose: () => void;
  onDone: (o: PaymentsOverview) => void;
}) {
  const now = new Date();
  const months = [-1, 0].map((d) => isoDay(new Date(now.getFullYear(), now.getMonth() + d, 1)));
  const [period, setPeriod] = useState(months[0] ?? "");
  const [amount, setAmount] = useState("");
  const [due, setDue] = useState(isoDay(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 7)));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(close: () => void) {
    setBusy(true);
    setError(null);
    try {
      onDone(
        await api.addUtilityCharge(leaseId, {
          period_month: period,
          amount: amount.replace(",", "."),
          due_date: due,
          note: note.trim() || null,
        }),
      );
      close();
    } catch (e) {
      setError(payError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet label={t("pay_util_title")} onClose={onClose}>
      {(close) => (
        <>
          <div className="sheet-head">
            <h2>{t("pay_util_title")}</h2>
            <button className="icon-button" aria-label={t("ab_close")} onClick={close}>
              <Icon name="close" size={20} />
            </button>
          </div>
          <section className="card">
            <p className="hint small">{t("pay_util_hint")}</p>
            <div className="field">
              {t("pay_util_month")}
              <div className="chips">
                {months.map((m) => (
                  <button key={m} type="button" className={`chip${period === m ? " active" : ""}`} aria-pressed={period === m} onClick={() => setPeriod(m)}>
                    {monthLabel(m)}
                  </button>
                ))}
              </div>
            </div>
            <label className="field">
              {t("pay_util_amount")}
              <input className="input" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
            </label>
            <label className="field">
              {t("pay_util_due")}
              <input className="input" type="date" min={isoDay(now)} value={due} onChange={(e) => setDue(e.target.value)} />
            </label>
            <label className="field">
              {t("pay_util_note")}
              <input className="input" maxLength={300} placeholder={t("pay_util_note_ph")} value={note} onChange={(e) => setNote(e.target.value)} />
            </label>
            <button className="button" disabled={busy || !(Number(amount.replace(",", ".")) > 0) || !due} onClick={() => save(close)}>
              {t("pay_util_save")}
            </button>
          </section>
          {error && <p className="error">{error}</p>}
        </>
      )}
    </Sheet>
  );
}

const TONE: Record<Charge["status"], "neutral" | "ok" | "warn" | "info" | "bad"> = {
  upcoming: "neutral",
  due: "warn",
  overdue: "bad",
  reported: "info",
  paid: "ok",
  disputed: "bad",
  waived: "neutral",
};

function payError(e: unknown): string {
  if (e instanceof ApiFailure && e.reason) return k(`pay_err_${e.reason}`);
  return errorText(e);
}

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

function CopyRow({ label, value, shown }: { label: string; value: string; shown?: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="copy-row">
      <span className="copy-label">{label}</span>
      <span className="copy-value">{shown ?? value}</span>
      <button
        className="copy-btn"
        onClick={async () => {
          if (await copy(value)) {
            setDone(true);
            window.setTimeout(() => setDone(false), 1500);
          }
        }}
      >
        {done ? t("pay_copied") : t("pay_copy")}
      </button>
    </div>
  );
}

/** PAY-REQ: що й куди платити + позначити оплату. */
function PaySheet({
  charge,
  address,
  webApp,
  onClose,
  onDone,
}: {
  charge: Charge;
  address: string;
  webApp: WebApp;
  onClose: () => void;
  onDone: (o: PaymentsOverview) => void;
}) {
  const [info, setInfo] = useState<PayInfo | null>(null);
  const [amount, setAmount] = useState(String(Number(charge.remaining)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.payInfo(charge.id).then(setInfo).catch((e: unknown) => setError(payError(e)));
  }, [charge.id]);

  const purpose = t("pay_purpose", { address, period: periodLabel(charge) });

  async function run(action: () => Promise<PaymentsOverview>) {
    setBusy(true);
    setError(null);
    try {
      onDone(await action());
      onClose();
    } catch (e) {
      setError(payError(e));
    } finally {
      setBusy(false);
    }
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) return setError(t("pay_err_too_big"));
    let data: ArrayBuffer;
    try {
      data = await file.arrayBuffer();
    } catch {
      return setError(t("own_read_failed"));
    }
    const blob = new Blob([data], { type: file.type || "application/octet-stream" });
    await run(() => api.reportReceipt(charge.id, blob, file.name || "kvytantsiia", amount));
  }

  const cashOnly = info?.method === "cash";
  return (
    <Sheet label={t("pay_title")} onClose={onClose}>
      {(close) => (
        <>
        <div className="sheet-head">
          <h2>{t("pay_title_for", { period: periodLabel(charge) })}</h2>
          <button className="icon-button" aria-label={t("ab_close")} onClick={close}>
            <Icon name="close" size={20} />
          </button>
        </div>
        {!info && !error && <div className="skeleton" style={{ height: 180 }} />}
        {info && (
          <section className="card">
            <CopyRow label={t("pay_amount")} value={String(Number(info.amount))} shown={money(info.amount)} />
            {info.recipient && <CopyRow label={t("pay_recipient")} value={info.recipient} />}
            {info.card && <CopyRow label={t("pay_card")} value={info.card} shown={info.card.replace(/(\d{4})(?=\d)/g, "$1 ")} />}
            {info.iban && <CopyRow label={t("pay_iban")} value={info.iban} />}
            {!cashOnly && <CopyRow label={t("pay_purpose_label")} value={purpose} />}
            {info.jar_url && (
              <button className="button secondary" onClick={() => openLink(webApp, info.jar_url ?? "")}>
                {t("pay_open_jar")}
              </button>
            )}
            <p className="hint small">{cashOnly ? t("pay_cash_hint") : t("pay_bank_hint")}</p>
          </section>
        )}
        {info && (
          <section className="card">
            <label className="field">
              {t("pay_paid_amount")}
              <input className="input" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(",", "."))} />
            </label>
            {!cashOnly && (
              <label className={`button file-button${busy ? " disabled" : ""}`}>
                <Icon name="doc" size={20} />
                {t("pay_add_receipt")}
                <input type="file" accept="application/pdf,image/*" hidden onChange={onFile} />
              </label>
            )}
            <button
              className={cashOnly ? "button" : "button ghost"}
              disabled={busy || !(Number(amount) > 0)}
              onClick={async () => {
                if (!(await confirm(webApp, t("pay_cash_confirm", { amount: money(amount) })))) return;
                await run(() => api.reportCash(charge.id, amount));
              }}
            >
              {t("pay_cash")}
            </button>
          </section>
        )}
        {error && <p className="error">{error}</p>}
        </>
      )}
    </Sheet>
  );
}

/** Квитанція: PDF сторінками або фото, і «надіслати в чат». */
function ReceiptSheet({ payment, onClose }: { payment: PaymentItem; onClose: () => void }) {
  const [file, setFile] = useState<{ data: ArrayBuffer; mime: string } | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    let objectUrl: string | null = null;
    api
      .paymentReceipt(payment.id)
      .then((r) => {
        setFile(r);
        if (!r.mime.includes("pdf")) {
          objectUrl = URL.createObjectURL(new Blob([r.data], { type: r.mime }));
          setUrl(objectUrl);
        }
      })
      .catch((e: unknown) => setNote(errorText(e)));
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [payment.id]);
  return (
    <Sheet label={t("pay_receipt")} onClose={onClose}>
      {(close) => (
        <>
        <div className="sheet-head">
          <h2>{t("pay_receipt")}</h2>
          <button className="icon-button" aria-label={t("ab_close")} onClick={close}>
            <Icon name="close" size={20} />
          </button>
        </div>
        {!file && !note && <div className="skeleton pdf-skeleton" />}
        {file && file.mime.includes("pdf") && <PdfViewer data={file.data} />}
        {url && <img className="receipt-img" src={url} alt={t("pay_receipt")} />}
        <button
          className="button secondary"
          onClick={async () => {
            try {
              await api.sendReceipt(payment.id);
              setNote(t("ct_sent_pdf"));
            } catch (e) {
              setNote(errorText(e));
            }
          }}
        >
          {t("ab_save_to_chat")}
        </button>
        {note && <p className="hint small">{note}</p>}
        </>
      )}
    </Sheet>
  );
}

function PaymentLine({
  p,
  landlord,
  busy,
  onDecide,
  onReceipt,
}: {
  p: PaymentItem;
  landlord: boolean;
  busy: boolean;
  onDecide: (accept: boolean) => void;
  onReceipt: () => void;
}) {
  return (
    <div className="pay-line">
      <div className="pay-line-head">
        <span>
          {money(p.amount)} · {k(`pay_method_${p.method}`)} · {p.payer_name}
        </span>
        <Chip tone={p.status === "confirmed" ? "ok" : p.status === "rejected" ? "bad" : "info"}>{k(`pay_p_${p.status}`)}</Chip>
      </div>
      {p.note && <p className="hint small">«{p.note}»</p>}
      {p.status === "auto_accepted" && (
        <p className="hint small">
          {t("pay_auto_note", {
            bank: p.receipt_bank ?? "",
            date: p.dispute_deadline ? formatDateTime(p.dispute_deadline) : "",
          })}
        </p>
      )}
      {p.mismatches.length > 0 && p.status !== "confirmed" && (
        <p className="hint small">
          {p.mismatches.includes("unparsed")
            ? t("pay_mm_unparsed")
            : t("pay_mm_list", { list: p.mismatches.map((m) => k(`pay_mm_${m}`)).join(", ") })}
        </p>
      )}
      {p.dispute_reason && <p className="error small">{p.dispute_reason}</p>}
      <div className="pay-line-actions">
        {p.has_receipt && (
          <button className="link" onClick={onReceipt}>
            {t("pay_show_receipt")}
          </button>
        )}
        {landlord && (p.status === "pending_landlord" || p.status === "auto_accepted") && (
          <>
            <button className="button" disabled={busy} onClick={() => onDecide(true)}>
              <Icon name="check" size={18} />
              {t("pay_received")}
            </button>
            <button className="button ghost" disabled={busy} onClick={() => onDecide(false)}>
              {t("pay_not_received")}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export function PaymentsBlock({
  leaseId,
  address,
  webApp,
  live = true,
}: {
  leaseId: string;
  address: string;
  webApp: WebApp;
  live?: boolean;
}) {
  const [o, setO] = useState<PaymentsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [paying, setPaying] = useState<Charge | null>(null);
  const [receipt, setReceipt] = useState<PaymentItem | null>(null);
  const [all, setAll] = useState(false);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [utility, setUtility] = useState(false);

  const load = useCallback(() => {
    api.payments(leaseId).then(setO).catch((e: unknown) => setError(errorText(e)));
  }, [leaseId]);
  useEffect(load, [load]);

  async function run(action: () => Promise<PaymentsOverview>) {
    setBusy(true);
    setError(null);
    try {
      setO(await action());
    } catch (e) {
      setError(payError(e));
    } finally {
      setBusy(false);
    }
  }

  if (!o) return error ? <p className="error">{error}</p> : <div className="skeleton" style={{ height: 120 }} />;

  const landlord = o.my_role === "landlord";
  const next = o.next_charge;
  const waiting = (status: PaymentItem["status"]) =>
    o.charges.flatMap((c) => c.payments.filter((p) => p.status === status).map((p) => ({ c, p })));
  const pending = waiting("pending_landlord");
  const auto = waiting("auto_accepted");
  const history = all ? o.charges : o.charges.slice(0, 4);

  return (
    <section className="card">
      <h2>{t("pay_section")}</h2>

      {next ? (
        <div className="pay-next">
          <div className="pay-next-head">
            <span>
              <b>{periodLabel(next)}</b> · {money(next.remaining)}
            </span>
            <Chip tone={TONE[next.status]}>{k(`pay_c_${next.status}`)}</Chip>
          </div>
          <p className="hint small">{t("pay_due_on", { date: formatDate(next.due_date) })}</p>
          {!landlord && next.status !== "reported" && (
            <button className="button" onClick={() => setPaying(next)}>
              <Icon name="send" size={20} />
              {t("pay_pay")}
            </button>
          )}
          {!landlord && next.status === "reported" && <p className="hint small">{t("pay_reported_tip")}</p>}
          {landlord && Number(next.remaining) > 0 && next.status !== "reported" && (
            <button
              className="button secondary"
              disabled={busy}
              onClick={async () => {
                if (!(await confirm(webApp, t("pay_mark_received_confirm", { amount: money(next.remaining), period: periodLabel(next) })))) return;
                await run(() => api.markReceived(next.id, String(Number(next.remaining))));
              }}
            >
              {t("pay_mark_received")}
            </button>
          )}
        </div>
      ) : (
        <p className="hint">{o.charges.length ? t("pay_all_clear") : t("pay_nothing_yet")}</p>
      )}

      {landlord &&
        ([
          ["pay_pending_title", pending],
          ["pay_auto_title", auto],
        ] as const).map(([title, list]) =>
          list.length === 0 ? null : (
        <div className="pay-pending" key={title}>
          <h3>{t(title)}</h3>
          {list.map(({ c, p }) => (
            <div key={p.id}>
              <p className="small">
                <b>{periodLabel(c)}</b>
              </p>
              <PaymentLine
                p={p}
                landlord
                busy={busy}
                onReceipt={() => setReceipt(p)}
                onDecide={(accept) => {
                  if (accept) void run(() => api.decidePayment(p.id, true));
                  else setRejecting(p.id);
                }}
              />
              {rejecting === p.id && (
                <div className="field">
                  <textarea className="input" rows={2} maxLength={500} placeholder={t("pay_reject_placeholder")} value={reason} onChange={(e) => setReason(e.target.value)} />
                  <button
                    className="button"
                    disabled={busy || reason.trim().length < 3}
                    onClick={async () => {
                      await run(() => api.decidePayment(p.id, false, reason));
                      setRejecting(null);
                      setReason("");
                    }}
                  >
                    {t("pay_reject_send")}
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
          ),
        )}

      {o.charges.length > 0 && (
        <div className="pay-history">
          <h3>{t("pay_history")}</h3>
          {history.map((c) => (
            <details key={c.id} className="pay-row">
              <summary>
                <span>{periodLabel(c)}</span>
                <span className="pay-row-amount">{money(c.amount)}</span>
                <Chip tone={TONE[c.status]}>{k(`pay_c_${c.status}`)}</Chip>
              </summary>
              <p className="hint small">{t("pay_due_on", { date: formatDate(c.due_date) })}</p>
              {c.note && <p className="hint small">{c.note}</p>}
              {c.payments.length === 0 && <p className="hint small">{t("pay_no_payments")}</p>}
              {c.can_cancel && (
                <button
                  className="link danger"
                  disabled={busy}
                  onClick={async () => {
                    if (!(await confirm(webApp, t("pay_util_cancel_confirm")))) return;
                    await run(() => api.cancelCharge(c.id));
                  }}
                >
                  {t("pay_util_cancel")}
                </button>
              )}
              {c.payments.map((p) => (
                <PaymentLine key={p.id} p={p} landlord={false} busy={busy} onDecide={() => undefined} onReceipt={() => setReceipt(p)} />
              ))}
            </details>
          ))}
          {o.charges.length > 4 && (
            <button className="link" onClick={() => setAll((v) => !v)}>
              {all ? t("pay_less") : t("pay_more", { n: String(o.charges.length - 4) })}
            </button>
          )}
        </div>
      )}

      {landlord && live && (
        <button className="button secondary" onClick={() => setUtility(true)}>
          <Icon name="plus" size={20} />
          {t("pay_add_utilities")}
        </button>
      )}

      {error && <p className="error">{error}</p>}
      {utility && <UtilitySheet leaseId={leaseId} onClose={() => setUtility(false)} onDone={setO} />}
      {paying && <PaySheet charge={paying} address={address} webApp={webApp} onClose={() => setPaying(null)} onDone={setO} />}
      {receipt && <ReceiptSheet payment={receipt} onClose={() => setReceipt(null)} />}
    </section>
  );
}
