// Договір у картці оренди (M4, BOT_FLOWS §8): ПІБ сторін → PDF → підписи на порталі Дії.
// «Договір уже є» (CTR-EXT) — для оренди за запрошенням.

import { useCallback, useEffect, useState } from "react";

import { api, ApiFailure, type ContractParty, type ContractState } from "../api/client";
import { botLink } from "../config";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import { openLink, type WebApp, confirm } from "../telegram";
import { errorCode, errorText, formatDate, MascotTip } from "../ui";

const k = (key: string) => t(key as TextKey);

/** Спершу читаємо файл повністю: на телефонах потокове читання може обірватися. */
async function readFile(file: File, type: string): Promise<Blob | null> {
  try {
    const data = await file.arrayBuffer();
    return data.byteLength ? new Blob([data], { type }) : null;
  } catch {
    return null;
  }
}

function contractError(e: unknown): string {
  if (e instanceof ApiFailure) {
    if (e.code === "invalid_input" && e.reason) return k(`ct_err_${e.reason}`);
    if (e.code === "conflict" && e.reason === "stale") return t("ct_err_stale");
    if (e.code === "banned") return t("ct_err_banned");
    if (e.code === "lease_limit") return t("ls_invite_lease_limit");
    if (e.code === "service_unavailable") return t("ct_err_unavailable");
    if (errorCode(e) === "invalid_input" && e.field === "full_name") return t("ct_err_name");
  }
  return errorText(e);
}

function SignatureLine({ party }: { party: ContractParty }) {
  const sig = party.signature;
  const state = sig ? sig.state : "none";
  const icon = state === "accepted" ? "✅" : state === "review" ? "⏳" : state === "rejected" ? "❌" : "✍️";
  return (
    <div className="unit">
      <span>
        {icon} {party.legal_name ?? party.display_name}
      </span>
      <span className="hint small">{k(`ct_sig_${state}`)}</span>
    </div>
  );
}

function NameForm({
  hint,
  busy,
  onSave,
}: {
  hint: string | null;
  busy: boolean;
  onSave: (name: string) => void;
}) {
  const [name, setName] = useState(hint ?? "");
  return (
    <div className="field">
      <label className="field">
        {t("ct_name_label")}
        <input
          className="input"
          value={name}
          maxLength={120}
          autoComplete="name"
          placeholder={t("ct_name_placeholder")}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      {hint && <p className="hint small">{t("ct_name_hint_doc")}</p>}
      <button className="button secondary" disabled={busy || name.trim().split(/\s+/).length < 2} onClick={() => onSave(name)}>
        {t("ct_name_save")}
      </button>
    </div>
  );
}

export function ContractBlock({
  leaseId,
  webApp,
  onActivated,
}: {
  leaseId: string;
  webApp: WebApp;
  onActivated: () => void;
}) {
  const [s, setS] = useState<ContractState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [editName, setEditName] = useState(false);

  useEffect(() => {
    api.contract(leaseId).then(setS).catch((e: unknown) => setError(contractError(e)));
  }, [leaseId]);

  const run = useCallback(
    async (action: () => Promise<ContractState | void>, done?: string) => {
      setBusy(true);
      setError(null);
      setNote(null);
      try {
        const r = await action();
        if (r) {
          setS(r);
          if (r.lease_status === "active") onActivated();
        }
        if (done) setNote(done);
      } catch (e) {
        setError(contractError(e));
      } finally {
        setBusy(false);
      }
    },
    [onActivated],
  );

  if (!s) {
    return error ? <p className="error">{error}</p> : <div className="skeleton" style={{ height: 120 }} />;
  }

  const c = s.contract;
  const me = s.parties.find((p) => p.is_me) ?? null;
  const other = s.parties.find((p) => !p.is_me) ?? null;
  const live = c && ["sent", "partially_signed"].includes(c.status) && !s.stale;
  const signed = c?.status === "signed";
  const generated = c?.source === "generated";

  async function onSignature(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !c) return;
    if (file.size > 5 * 1024 * 1024) return setError(t("ct_err_too_big"));
    const blob = await readFile(file, "application/octet-stream");
    if (!blob) return setError(t("own_read_failed"));
    await run(() => api.uploadSignature(c.id, blob, file.name || "signature.p7s"));
  }

  async function onExternal(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) return setError(t("ct_err_external"));
    const blob = await readFile(file, "application/pdf");
    if (!blob) return setError(t("own_read_failed"));
    await run(() => api.uploadExternal(leaseId, blob, file.name || "dogovir.pdf"));
  }

  const sendPdf = () =>
    c && run(async () => void (await api.sendContract(c.id, "pdf")), t("ct_sent_pdf"));

  // ------------------------------------------------------ підписано
  if (signed && c) {
    return (
      <section className="card">
        <h2>{t("ct_title")}</h2>
        <p>{c.signed_at ? t("ct_signed_on", { date: formatDate(c.signed_at) }) : t("ct_signed")}</p>
        {generated && s.parties.map((p) => <SignatureLine key={p.user_id} party={p} />)}
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => run(async () => void (await api.sendContract(c.id, generated ? "package" : "pdf")), t("ct_sent_package"))}
        >
          <Icon name="doc" size={20} />
          {generated ? t("ct_send_package") : t("ct_send_pdf")}
        </button>
        {note && <p className="hint small">{note}</p>}
        {error && <p className="error">{error}</p>}
      </section>
    );
  }

  if (s.lease_status !== "awaiting_signatures") return null;

  // ------------------------------------------- CTR-EXT: орендар підтверджує
  if (live && c && !generated) {
    return (
      <section className="card accent">
        <h2>{t("ct_title")}</h2>
        {s.awaiting_my_confirmation ? (
          <>
            <MascotTip tone="calm">
              <p>{t("ct_ext_confirm_tip")}</p>
            </MascotTip>
            <button className="button secondary" disabled={busy} onClick={sendPdf}>
              <Icon name="doc" size={20} />
              {t("ct_send_pdf")}
            </button>
            <button className="button" disabled={busy} onClick={() => run(() => api.decideExternal(c.id, true))}>
              <Icon name="check" size={20} />
              {t("ct_ext_yes")}
            </button>
            <button
              className="button ghost"
              disabled={busy}
              onClick={async () => {
                if (!(await confirm(webApp, t("ct_ext_no_confirm")))) return;
                await run(() => api.decideExternal(c.id, false));
              }}
            >
              {t("ct_ext_no")}
            </button>
          </>
        ) : (
          <p className="hint">{t("ct_ext_wait")}</p>
        )}
        {note && <p className="hint small">{note}</p>}
        {error && <p className="error">{error}</p>}
      </section>
    );
  }

  const myName = me?.legal_name ?? null;
  const showNameForm = me && (!myName || editName) && !(live && me.signature);

  return (
    <section className="card accent">
      <h2>{t("ct_title")}</h2>

      {s.stale && <p className="hint">{t("ct_stale")}</p>}

      {/* Крок 1: ПІБ сторін для тексту договору */}
      {!live && (
        <>
          <p className="hint">{t("ct_names_tip")}</p>
          {s.parties.map((p) => (
            <div key={p.user_id} className="unit">
              <span>{p.legal_name ?? (p.is_me ? t("ct_name_missing_me") : t("ct_name_missing", { name: p.display_name }))}</span>
              <span className="hint small">{k(`ct_party_${p.role}`)}</span>
            </div>
          ))}
          {showNameForm && (
            <NameForm
              hint={myName ?? s.landlord_name_hint}
              busy={busy}
              onSave={(name) =>
                run(async () => {
                  const r = await api.setLegalName(leaseId, name);
                  setEditName(false);
                  return r;
                })
              }
            />
          )}
          {me && myName && !editName && (
            <button className="link" onClick={() => setEditName(true)}>
              {t("ct_name_edit")}
            </button>
          )}
        </>
      )}

      {/* Крок 2: власник формує договір */}
      {s.can_generate && !live && (
        <>
          {s.needs_names ? (
            <p className="hint small">{t("ct_need_names")}</p>
          ) : (
            <p className="hint small">{t("ct_generate_tip")}</p>
          )}
          <button className="button" disabled={busy || s.needs_names} onClick={() => run(() => api.generateContract(leaseId))}>
            <Icon name="doc" size={20} />
            {busy ? t("ct_generating") : c ? t("ct_regenerate") : t("ct_generate")}
          </button>
          {s.can_upload_external && (
            <label className="button ghost file-button">
              {t("ct_have_contract")}
              <input type="file" accept="application/pdf,.pdf" hidden onChange={onExternal} />
            </label>
          )}
        </>
      )}
      {!s.can_generate && !live && s.my_role === "responsible" && !s.needs_names && (
        <p className="hint small">{t("ct_wait_landlord")}</p>
      )}

      {/* Крок 3: підписи на порталі Дії */}
      {live && c && (
        <>
          <ol className="steps">
            <li>{t("ct_step_1")}</li>
            <li>{t("ct_step_2")}</li>
            <li>{t("ct_step_3")}</li>
            <li>{t("ct_step_4")}</li>
          </ol>
          <p className="hint small">{t("ct_same_file")}</p>
          {s.parties.map((p) => (
            <SignatureLine key={p.user_id} party={p} />
          ))}
          {me?.signature?.state === "rejected" && me.signature.review_reason && (
            <p className="error">{t("ct_rejected_reason", { reason: me.signature.review_reason })}</p>
          )}
          {me?.signature?.state === "review" && s.require_moderator && (
            <p className="hint small">{t("ct_review_tip")}</p>
          )}
          {s.can_sign && (
            <>
              <button className="button secondary" disabled={busy} onClick={sendPdf}>
                <Icon name="doc" size={20} />
                {t("ct_send_pdf")}
              </button>
              <button className="button secondary" onClick={() => openLink(webApp, s.diia_sign_url)}>
                {t("ct_open_diia")}
              </button>
              <label className={`button file-button${busy ? " disabled" : ""}`}>
                <Icon name="send" size={20} />
                {t("ct_upload_signature")}
                <input type="file" accept=".p7s,.p7m,.asice,.sce,application/pkcs7-signature" hidden onChange={onSignature} />
              </label>
              {botLink(`sig_${c.id}`) && (
                <button className="link" onClick={() => openLink(webApp, botLink(`sig_${c.id}`))}>
                  {t("ct_via_bot")}
                </button>
              )}
            </>
          )}
          {!s.can_sign && other && !other.signature && <p className="hint small">{t("ct_wait_other", { name: other.display_name })}</p>}
        </>
      )}

      {note && <p className="hint small">{note}</p>}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
