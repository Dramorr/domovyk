// Модератор: черга перевірок власності (BOT_FLOWS §17, MOD-OWN; FR-MOD-1).

import { useEffect, useState } from "react";

import { api, type QueueItem, type SignatureCase, type VerificationCase } from "../api/client";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import {
  Chip,
  errorCode,
  errorField,
  errorText,
  formatAddress,
  formatDate,
  ListRow,
  MascotTip,
  Ring,
  roomsText,
  Screen,
} from "../ui";

/** Збіг / розбіжність поля — іконкою замість ✓/✗. */
function Match({ ok }: { ok: boolean }) {
  return (
    <span className={`match ${ok ? "ok" : "bad"}`} aria-label={t(ok ? "mod_match_yes" : "mod_match_no")}>
      <Icon name={ok ? "check" : "close"} size={16} />
    </span>
  );
}

export function ModerationQueue({
  onBack,
  onOpen,
  onOpenSignature,
}: {
  onBack?: () => void;
  onOpen: (vid: string) => void;
  onOpenSignature: (sid: string) => void;
}) {
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.ownershipQueue().then(setItems).catch((e: unknown) => setError(errorText(e)));
  }, []);

  return (
    <Screen onBack={onBack}>
      <h1>{t("mod_title")}</h1>
      <p className="section-title">{t("mod_queue_title_plain")}</p>
      {error && <p className="error">{error}</p>}
      {!items && !error && <div className="skeleton" />}
      {items && items.length === 0 && (
        <MascotTip>
          <p>{t("mod_queue_empty")}</p>
        </MascotTip>
      )}
      {items?.map((q) => (
        <ListRow
          key={q.verification_id}
          icon="doc"
          title={formatAddress(q.property)}
          sub={
            <>
              <span>{q.owner.display_name}</span>
              <span>· {t("mod_docs", { n: String(q.documents_count) })}</span>
              <span>· {formatDate(q.submitted_at)}</span>
              {q.owner.risk_signals > 0 && <Chip tone="bad">{t("mod_risk", { n: String(q.owner.risk_signals) })}</Chip>}
            </>
          }
          right={q.property.completeness && <Ring percent={q.property.completeness.percent} size={44} />}
          onClick={() => onOpen(q.verification_id)}
        />
      ))}
      <p className="section-title">{t("mod_sig_title")}</p>
      <SignatureQueue onOpen={onOpenSignature} />
    </Screen>
  );
}

const REASONS: TextKey[] = ["mod_reason_address", "mod_reason_unreadable", "mod_reason_wrong_doc"];

export function ModerationCase({ vid, onBack }: { vid: string; onBack: () => void }) {
  const [item, setItem] = useState<VerificationCase | null>(null);
  const [ownerName, setOwnerName] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [otherReason, setOtherReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api.ownershipCase(vid).then(setItem).catch((e: unknown) => setError(errorText(e)));
  }, [vid]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await action();
    } catch (e) {
      if (errorCode(e) === "permission_denied") setError(t("mod_own_property"));
      else if (errorCode(e) === "invalid_input" && errorField(e) === "owner_name") setError(t("mod_owner_name"));
      else setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (!item) {
    return (
      <Screen onBack={onBack}>
        {error ? <p className="error">{error}</p> : <p className="hint">{t("loading")}</p>}
      </Screen>
    );
  }

  const { property: p, owner, verification: v } = item;
  const open = v.status === "pending";

  const reject = (reason: string) =>
    run(async () => {
      setItem(await api.rejectOwnership(vid, reason));
      setRejecting(false);
      setMessage(t("mod_done_rejected"));
    });

  return (
    <Screen onBack={onBack}>
      <h1 className="left">{formatAddress(p)}</h1>
      <p className="hint left">
        {roomsText(p.rooms)}
        {p.area && ` · ${t("sqm", { area: p.area })}`}
        {p.district && ` · ${p.district}`}
      </p>

      <section className="card">
        <h2>{t("mod_owner")}</h2>
        <p>{owner.display_name}</p>
        <p className="hint small">
          {t("mod_registered", { date: formatDate(owner.registered_at) })} ·{" "}
          {t("mod_owner_props", { n: String(owner.properties_count) })}
        </p>
        {owner.risk_signals > 0 && <p className="error small">{t("mod_risk", { n: String(owner.risk_signals) })}</p>}
      </section>

      <section className="card">
        <h2>{t("mod_docs", { n: String(v.documents.length) })}</h2>
        {v.documents.map((d) => (
          <p key={d.id} className="small">
            <Icon name="doc" size={16} /> {d.filename} {d.deleted && <span className="hint">({t("own_file_deleted")})</span>}
          </p>
        ))}
        {v.documents.some((d) => !d.deleted) && (
          <button
            className="button secondary"
            disabled={busy}
            onClick={() =>
              run(async () => {
                const { sent } = await api.sendDocuments(vid);
                setMessage(t("mod_docs_sent", { n: String(sent) }));
              })
            }
          >
            <Icon name="send" size={20} />
            {t("mod_send_docs")}
          </button>
        )}
      </section>

      {!open && (
        <p className={v.status === "verified" ? "ok" : "error"}>
          {t(`own_status_${v.status === "verified" ? "verified" : "rejected"}` as TextKey)}
          {v.reject_reason && `: ${v.reject_reason}`}
        </p>
      )}

      {open && !rejecting && (
        <section className="card">
          <MascotTip tone="calm">
            <p>{t("mod_check_hint")}</p>
          </MascotTip>
          <label className="field">
            {t("mod_owner_name")}
            <input className="input" value={ownerName} maxLength={300} onChange={(e) => setOwnerName(e.target.value)} />
          </label>
          <button
            className="button"
            disabled={busy || ownerName.trim().length < 3}
            onClick={() =>
              run(async () => {
                setItem(await api.approveOwnership(vid, ownerName));
                setMessage(t("mod_done_approved"));
              })
            }
          >
            <Icon name="check" size={20} />
            {t("mod_approve")}
          </button>
          <button className="button secondary" disabled={busy} onClick={() => setRejecting(true)}>
            {t("mod_reject")}
          </button>
        </section>
      )}

      {open && rejecting && (
        <section className="card">
          <h2>{t("mod_reject_title")}</h2>
          <div className="choices">
            {REASONS.map((key) => (
              <button key={key} className="button secondary" disabled={busy} onClick={() => reject(t(key))}>
                {t(key)}
              </button>
            ))}
          </div>
          <label className="field">
            {t("mod_reason_other")}
            <input
              className="input"
              value={otherReason}
              maxLength={500}
              placeholder={t("mod_reason_placeholder")}
              onChange={(e) => setOtherReason(e.target.value)}
            />
          </label>
          <button className="button" disabled={busy || otherReason.trim().length < 2} onClick={() => reject(otherReason)}>
            {t("mod_reject")}
          </button>
          <button className="link" onClick={() => setRejecting(false)}>
            {t("cancel")}
          </button>
        </section>
      )}

      {message && <p className="ok">{message}</p>}
      {error && <p className="error">{error}</p>}
    </Screen>
  );
}

// ------------------------------------------- підписи під договорами (ADR 0010)

export function SignatureQueue({ onOpen }: { onOpen: (sid: string) => void }) {
  const [items, setItems] = useState<SignatureCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.signatureQueue().then(setItems).catch((e: unknown) => setError(errorText(e)));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!items) return <div className="skeleton" />;
  if (items.length === 0) return <p className="hint small">{t("mod_sig_empty")}</p>;
  return (
    <>
      {items.map((c) => (
        <ListRow
          key={c.signature_id}
          icon="doc"
          title={formatAddress(c)}
          sub={
            <>
              <span>{c.display_name}</span>
              <span>· {t(`mod_sig_role_${c.party_role}` as TextKey)}</span>
              <span>· {formatDate(c.uploaded_at)}</span>
              {c.check_status === "mismatch" && <Chip tone="bad">{t("mod_sig_mismatch")}</Chip>}
            </>
          }
          onClick={() => onOpen(c.signature_id)}
        />
      ))}
    </>
  );
}

const SIG_REASONS: TextKey[] = ["mod_sig_reason_invalid", "mod_sig_reason_name", "mod_sig_reason_document"];

export function SignatureReview({ sid, onBack }: { sid: string; onBack: () => void }) {
  const [item, setItem] = useState<SignatureCase | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [otherReason, setOtherReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api.signatureCase(sid).then(setItem).catch((e: unknown) => setError(errorText(e)));
  }, [sid]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await action();
    } catch (e) {
      setError(errorCode(e) === "permission_denied" ? t("mod_sig_own") : errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (!item) {
    return (
      <Screen onBack={onBack}>
        {error ? <p className="error">{error}</p> : <p className="hint">{t("loading")}</p>}
      </Screen>
    );
  }

  const open = ["not_checked", "auto_ok", "mismatch"].includes(item.check_status);
  const decide = (approve: boolean, reason?: string) =>
    run(async () => {
      setItem(await api.reviewSignature(sid, approve, reason));
      setRejecting(false);
      setMessage(approve ? t("mod_sig_done_ok") : t("mod_done_rejected"));
    });

  return (
    <Screen onBack={onBack}>
      <h1 className="left">{formatAddress(item)}</h1>
      <p className="hint left">
        {item.display_name} · {t(`mod_sig_role_${item.party_role}` as TextKey)}
      </p>

      <section className="card">
        <h2>{t("mod_sig_compare")}</h2>
        <div className="unit">
          <span className="hint small">{t("mod_sig_in_cert")}</span>
          <span>{item.signer_name ?? "—"}</span>
        </div>
        <div className="unit">
          <span className="hint small">{t("mod_sig_in_contract")}</span>
          <span>
            {item.expected_name ?? "—"} <Match ok={item.contract_name_match} />
          </span>
        </div>
        {item.party_role === "landlord" && (
          <div className="unit">
            <span className="hint small">{t("mod_sig_in_doc")}</span>
            <span>
              {item.owner_name_in_doc ?? "—"} {item.owner_doc_match != null && <Match ok={item.owner_doc_match} />}
            </span>
          </div>
        )}
        {item.contract_name_match && item.owner_doc_match === false && (
          <p className="hint small">{t("mod_sig_doc_name_hint")}</p>
        )}
        <p className="small">
          {t(`mod_sig_doc_${item.document_match}` as TextKey)}
          {item.digest_algorithm && ` · ${item.digest_algorithm}`}
          {item.container && ` · ${item.container}`}
        </p>
        {item.issuer && <p className="hint small">{t("mod_sig_issuer", { issuer: item.issuer })}</p>}
        {item.signed_at_claimed && <p className="hint small">{t("mod_sig_signed_at", { date: formatDate(item.signed_at_claimed) })}</p>}
        <button
          className="button secondary"
          disabled={busy}
          onClick={() =>
            run(async () => {
              const { sent } = await api.sendSignatureFiles(sid);
              setMessage(t("mod_docs_sent", { n: String(sent) }));
            })
          }
        >
          {t("mod_sig_send")}
        </button>
      </section>

      {!open && (
        <p className={item.check_status === "manual_ok" ? "ok" : "error"}>{t(`mod_sig_status_${item.check_status}` as TextKey)}</p>
      )}

      {open && !rejecting && (
        <section className="card">
          <MascotTip tone="calm">
            <p>{t("mod_sig_check_hint")}</p>
          </MascotTip>
          <button className="button" disabled={busy} onClick={() => decide(true)}>
            {t("mod_sig_approve")}
          </button>
          <button className="button secondary" disabled={busy} onClick={() => setRejecting(true)}>
            {t("mod_reject")}
          </button>
        </section>
      )}

      {open && rejecting && (
        <section className="card">
          <h2>{t("mod_reject_title")}</h2>
          <div className="choices">
            {SIG_REASONS.map((key) => (
              <button key={key} className="button secondary" disabled={busy} onClick={() => decide(false, t(key))}>
                {t(key)}
              </button>
            ))}
          </div>
          <label className="field">
            {t("mod_reason_other")}
            <input
              className="input"
              value={otherReason}
              maxLength={500}
              placeholder={t("mod_reason_placeholder")}
              onChange={(e) => setOtherReason(e.target.value)}
            />
          </label>
          <button className="button" disabled={busy || otherReason.trim().length < 2} onClick={() => decide(false, otherReason)}>
            {t("mod_reject")}
          </button>
          <button className="link" onClick={() => setRejecting(false)}>
            {t("cancel")}
          </button>
        </section>
      )}

      {message && <p className="ok">{message}</p>}
      {error && <p className="error">{error}</p>}
    </Screen>
  );
}
