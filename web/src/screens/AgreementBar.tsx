// Смужка прогресу погодження договору (FR-LSE-10): ліворуч орендар, праворуч власник,
// посередині — договір. Кожна половина має два кроки: умови → підпис.

import { useEffect, useState } from "react";

import { api, type ContractState, type Lease } from "../api/client";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import { errorText, formatDate } from "../ui";
import { PdfViewer } from "./PdfViewer";

const k = (key: string) => t(key as TextKey);

type Tone = "empty" | "ok" | "pending" | "warn";
type Side = { terms: Tone; sign: Tone; label: string };

function termsTone(state: Lease["agreement"]["tenant"]): Tone {
  if (state === "agreed") return "ok";
  if (state === "proposed" || state === "declined") return "warn";
  return "empty";
}

function signTone(sig: ContractState["parties"][number]["signature"] | undefined): Tone {
  if (!sig) return "empty";
  if (sig.state === "accepted") return "ok";
  if (sig.state === "rejected") return "warn";
  return "pending";
}

function sideOf(lease: Lease, contract: ContractState | null, role: "tenant" | "landlord"): Side {
  const agreement = role === "tenant" ? lease.agreement.tenant : lease.agreement.landlord;
  const party = contract?.parties.find((p) => p.role === role);
  const terms = termsTone(agreement);
  const sign = lease.status === "active" ? "ok" : signTone(party?.signature);
  let label: string;
  if (agreement === "proposed") label = t("ab_proposed");
  else if (agreement === "declined") label = t("ab_declined");
  else if (sign === "ok") label = t("ab_signed");
  else if (sign === "pending") label = t("ab_sign_review");
  else if (sign === "warn") label = t("ab_sign_rejected");
  else if (terms === "ok") label = t("ab_agreed");
  else label = t("ab_waiting");
  return { terms, sign, label };
}

export function AgreementBar({
  lease,
  contract,
  onOpen,
}: {
  lease: Lease;
  contract: ContractState | null;
  onOpen: () => void;
}) {
  const tenant = sideOf(lease, contract, "tenant");
  const landlord = sideOf(lease, contract, "landlord");
  const tones = [tenant.terms, tenant.sign, landlord.terms, landlord.sign];
  const hub: Tone = tones.every((x) => x === "ok")
    ? "ok"
    : tones.includes("warn")
      ? "warn"
      : tones.some((x) => x !== "empty")
        ? "pending"
        : "empty";
  const tenantName = lease.members.find((m) => m.role === "responsible")?.display_name ?? t("ab_tenant");
  const landlordName = lease.members.find((m) => m.role === "landlord")?.display_name ?? t("ab_landlord");

  return (
    <div className="agreement-bar" role="group" aria-label={t("ab_title")}>
      <div className="ab-labels">
        <span>
          <b>{tenantName}</b>
          <br />
          {tenant.label}
        </span>
        <span className="right">
          <b>{landlordName}</b>
          <br />
          {landlord.label}
        </span>
      </div>
      <div className="ab-track">
        <div className="ab-half left">
          <span className={`ab-seg is-${tenant.terms}`} />
          <span className={`ab-seg is-${tenant.sign}`} />
        </div>
        <button className={`ab-hub is-${hub}`} onClick={onOpen} aria-label={t("ab_open")}>
          <Icon name="doc" size={18} />
        </button>
        <div className="ab-half right">
          <span className={`ab-seg is-${landlord.sign}`} />
          <span className={`ab-seg is-${landlord.terms}`} />
        </div>
      </div>
    </div>
  );
}

function money(n: number): string {
  return `${n.toLocaleString("uk-UA")} ₴`;
}

/** «Актуальна версія»: поточні умови, запропоновані зміни й файл договору. */
export function AgreementSheet({
  lease,
  contract,
  termsLines,
  onClose,
}: {
  lease: Lease;
  contract: ContractState | null;
  termsLines: (terms: Lease["terms"]) => string[];
  onClose: () => void;
}) {
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pdf, setPdf] = useState<{ data: ArrayBuffer; draft: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api
      .contractPreview(lease.id)
      .then(setPdf)
      .catch((e: unknown) => setError(errorText(e)));
  }, [lease.id]);
  const p = lease.proposal;
  const c = contract?.contract;
  const live = c && c.status !== "void";

  const diff: string[] = [];
  if (p) {
    if (p.rent_amount !== lease.rent_amount) diff.push(t("ab_diff_rent", { from: money(lease.rent_amount), to: money(p.rent_amount) }));
    if (p.rent_due_day !== lease.rent_due_day) diff.push(t("ab_diff_due", { from: String(lease.rent_due_day), to: String(p.rent_due_day) }));
    if ((p.deposit_amount ?? 0) !== (lease.deposit_amount ?? 0))
      diff.push(t("ab_diff_deposit", { from: money(lease.deposit_amount ?? 0), to: money(p.deposit_amount ?? 0) }));
    if (p.start_date !== lease.start_date || p.end_date !== lease.end_date)
      diff.push(
        t("ab_diff_period", {
          from: `${formatDate(lease.start_date)} – ${formatDate(lease.end_date)}`,
          to: `${formatDate(p.start_date)} – ${formatDate(p.end_date)}`,
        }),
      );
    const before = termsLines(lease.terms);
    const after = termsLines(p.terms);
    after.forEach((line, i) => {
      if (line !== before[i]) diff.push(t("ab_diff_term", { to: line }));
    });
  }

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={t("ab_sheet_title")} onClick={(e) => e.stopPropagation()}>
        <div className="sheet-handle" />
        <div className="sheet-head">
          <h2>{t("ab_sheet_title")}</h2>
          {pdf && (
            <span className={`chip-status ${pdf.draft ? "warn" : "ok"}`}>
              {pdf.draft ? t("ab_draft") : t("ab_for_signing", { n: String(c?.version ?? 1) })}
            </span>
          )}
          <button className="icon-button" aria-label={t("ab_close")} onClick={onClose}>
            ✕
          </button>
        </div>
        {p && (
          <section className="card accent">
            <h3>{k(`ab_proposal_by_${p.by}`)}</h3>
            {diff.length ? (
              <ul className="plain">
                {diff.map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            ) : (
              <p className="hint">{t("ab_no_diff")}</p>
            )}
            <p className="hint small">{t("ab_proposal_pdf_hint")}</p>
          </section>
        )}
        {error && <p className="error">{error}</p>}
        {!pdf && !error && <div className="skeleton pdf-skeleton" />}
        {pdf && <PdfViewer data={pdf.data} />}
        {live && c && (
          <button
            className="button secondary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await api.sendContract(c.id, c.status === "signed" && c.source === "generated" ? "package" : "pdf");
                setNote(t("ct_sent_pdf"));
              } catch {
                setNote(t("ct_err_unavailable"));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Icon name="doc" size={20} />
            {t("ab_save_to_chat")}
          </button>
        )}
        {note && <p className="hint small">{note}</p>}
      </div>
    </div>
  );
}
