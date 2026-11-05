// Модератор: черга перевірок власності (BOT_FLOWS §17, MOD-OWN; FR-MOD-1).

import { useEffect, useState } from "react";

import { api, type QueueItem, type VerificationCase } from "../api/client";
import { t, type TextKey } from "../i18n";
import { errorCode, errorField, errorText, formatAddress, formatDate, roomsText, Screen } from "../ui";

export function ModerationQueue({ onBack, onOpen }: { onBack: () => void; onOpen: (vid: string) => void }) {
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.ownershipQueue().then(setItems).catch((e: unknown) => setError(errorText(e)));
  }, []);

  return (
    <Screen onBack={onBack}>
      <h1>{t("mod_title")}</h1>
      <h2 className="left">{t("mod_queue_title")}</h2>
      {error && <p className="error">{error}</p>}
      {items && items.length === 0 && <p className="hint">{t("mod_queue_empty")}</p>}
      {items?.map((q) => (
        <button key={q.verification_id} className="row" onClick={() => onOpen(q.verification_id)}>
          <span className="row-title">{formatAddress(q.property)}</span>
          <span className="row-sub">
            {q.owner.display_name} · {t("mod_docs", { n: String(q.documents_count) })} ·{" "}
            {t("mod_submitted", { date: formatDate(q.submitted_at) })}
            {q.owner.risk_signals > 0 && ` · ${t("mod_risk", { n: String(q.owner.risk_signals) })}`}
          </span>
        </button>
      ))}
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
      <h1 className="left">
        {p.kind === "house" ? "🏡" : "🏢"} {formatAddress(p)}
      </h1>
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
            📄 {d.filename} {d.deleted && <span className="hint">({t("own_file_deleted")})</span>}
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
          <p className="hint small">{t("mod_check_hint")}</p>
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
