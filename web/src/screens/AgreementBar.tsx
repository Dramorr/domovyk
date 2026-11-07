// Смужка прогресу погодження договору (FR-LSE-10): ліворуч орендар, праворуч власник,
// посередині — договір. Кожна половина має два кроки: умови → підпис.

import { useState } from "react";

import { api, type ContractState, type Lease } from "../api/client";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import { formatDate } from "../ui";

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
  const hub: Tone = tones.every((x) => x === "ok") ? "ok" : tones.includes("warn") ? "warn" : "empty";
  const tenantName = lease.members.find((m) => m.role === "responsible")?.display_name ?? t("ab_tenant");
  const landlordName = lease.members.find((m) => m.role === "landlord")?.display_name ?? t("ab_landlord");

  return (
    <div className="agreement-bar" role="group" aria-label={t("ab_title")}>
      <div className="ab-track">
        <div className="ab-half left">
          <span className={`ab-seg ${tenant.terms}`} />
          <span className={`ab-seg ${tenant.sign}`} />
        </div>
        <button className={`ab-hub ${hub}`} onClick={onOpen} aria-label={t("ab_open")}>
          <Icon name="doc" size={22} />
        </button>
        <div className="ab-half right">
          <span className={`ab-seg ${landlord.sign}`} />
          <span className={`ab-seg ${landlord.terms}`} />
        </div>
      </div>
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
        <h2>{t("ab_sheet_title")}</h2>
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
          </section>
        )}
        <section className="card">
          <h3>{p ? t("ab_current") : t("ab_terms")}</h3>
          <p>
            {t("ls_period", { from: formatDate(lease.start_date), to: formatDate(lease.end_date) })} ·{" "}
            {t("ls_rent_line", { rent: money(lease.rent_amount).replace(" ₴", ""), day: String(lease.rent_due_day) })}
          </p>
          {lease.deposit_amount ? <p className="hint">{t("ls_deposit_line", { sum: money(lease.deposit_amount).replace(" ₴", "") })}</p> : null}
          <ul className="plain">
            {termsLines(lease.terms).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </section>
        {live && c && (
          <section className="card">
            <h3>{t("ab_contract")}</h3>
            <p className="hint small">
              {t("ab_contract_version", { n: String(c.version), date: formatDate(c.created_at) })}
            </p>
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
              {t("ct_send_pdf")}
            </button>
            {note && <p className="hint small">{note}</p>}
          </section>
        )}
        <button className="button ghost" onClick={onClose}>
          {t("ab_close")}
        </button>
      </div>
    </div>
  );
}
