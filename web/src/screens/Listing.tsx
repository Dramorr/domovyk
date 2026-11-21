// Оголошення власника (M6, BOT_FLOWS §6, FR-LST-1..5, ADR 0018). Оголошення — лише умови
// угоди; опис, фото, правила й зручності беруться з анкети квартири.

import { useEffect, useState } from "react";

import { api, ApiFailure, type Listing, type ListingIn, type ListingState, type ListingStatus, type Property } from "../api/client";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import { confirm, type WebApp } from "../telegram";
import { Chip, errorCode, errorText, formatAddress, formatDate, MascotTip, Screen } from "../ui";
import { IncomingApplications } from "./Applications";
import { Chips } from "./Leases";
import { ShowingsBlock } from "./Showings";

const k = (key: string, vars?: Record<string, string>) => t(key as TextKey, vars);

const FIELD_ERRORS: Record<string, TextKey> = {
  price_amount: "lst_err_price_amount",
  deposit_amount: "lst_err_deposit_amount",
  min_term_months: "lst_err_min_term_months",
  available_from: "lst_err_available_from",
};

const TONE: Record<ListingStatus, "neutral" | "ok" | "warn" | "info" | "bad"> = {
  draft: "neutral",
  active: "ok",
  paused: "warn",
  rented: "info",
  archived: "neutral",
  blocked: "bad",
};

const LIVE: ListingStatus[] = ["draft", "active", "paused"];

export function ListingChip({ status }: { status: ListingStatus }) {
  return <Chip tone={TONE[status]}>{k(`lst_status_${status}`)}</Chip>;
}

type Form = { price: string; deposit: string; utilities: boolean; term: string; from: string };

/** «Вільна з»: у живого оголошення — його дата; у нового — підказка бекенду
 *  (сьогодні або день після кінця оренди). */
function toForm(l: Listing | null, suggestedFrom = ""): Form {
  const live = l !== null && LIVE.includes(l.status);
  return {
    price: l ? String(l.price_amount) : "",
    deposit: l?.deposit_amount != null ? String(l.deposit_amount) : "",
    utilities: l?.utilities_included ?? false,
    term: l?.min_term_months != null ? String(l.min_term_months) : "",
    from: (live && l.available_from) || suggestedFrom,
  };
}

function digits(raw: string): number | null {
  const clean = raw.replace(/\s/g, "");
  return clean === "" ? null : Number(clean);
}

function body(f: Form): ListingIn {
  return {
    price_amount: digits(f.price) ?? 0,
    utilities_included: f.utilities,
    deposit_amount: digits(f.deposit),
    min_term_months: digits(f.term),
    available_from: f.from || null,
  };
}

function listingError(e: unknown): string {
  switch (errorCode(e)) {
    case "declaration_required":
      return t("lst_err_declaration");
    case "ownership_not_verified":
      return t("lst_err_ownership");
    case "profile_incomplete":
      return t("lst_err_profile");
    case "unit_busy":
      return t("lst_err_busy");
  }
  const field = e instanceof ApiFailure && e.field ? FIELD_ERRORS[e.field] : undefined;
  return field ? t(field) : errorText(e);
}

export function ListingScreen({
  id,
  webApp,
  onBack,
  onOwnership,
  onProfile,
  onApplication,
  embedded = false,
  onChanged,
}: {
  id: string;
  webApp: WebApp;
  onBack: () => void;
  onOwnership: () => void;
  onProfile: () => void;
  onApplication: (id: string) => void;
  /** Вкладка «Оголошення» сторінки квартири (ADR 0023): без своєї шапки, заявок і показів. */
  embedded?: boolean;
  /** Стан оголошення змінився (опубліковано, пауза, знято) — сторінці квартири варто оновити етап. */
  onChanged?: () => void;
}) {
  const [prop, setProp] = useState<Property | null>(null);
  const [state, setState] = useState<ListingState | null>(null);
  const [form, setForm] = useState<Form>(toForm(null));
  const [declared, setDeclared] = useState(false);
  // Оголошення здано чи знято: показуємо підсумок, а форма — лише після «Нове оголошення».
  const [fresh, setFresh] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  function apply(s: ListingState) {
    setState(s);
    setForm(toForm(s.listing, s.suggested_available_from));
  }

  useEffect(() => {
    api.property(id).then(setProp).catch((e: unknown) => setError(errorText(e)));
    api.listing(id).then(apply).catch((e: unknown) => setError(errorText(e)));
  }, [id]);

  async function run(action: () => Promise<ListingState>, done?: () => void) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      apply(await action());
      setFresh(false);
      onChanged?.();
      done?.();
    } catch (e) {
      setError(listingError(e));
    } finally {
      setBusy(false);
    }
  }

  if (!state || !prop) {
    const wait = error ? <p className="error">{error}</p> : <p className="hint">{t("loading")}</p>;
    return embedded ? wait : <Screen onBack={onBack}>{wait}</Screen>;
  }

  const listing = state.listing;
  const r = state.readiness;
  const live = listing !== null && LIVE.includes(listing.status);
  const editing = live || fresh || listing === null;
  const status = live ? listing.status : null;
  const needDeclaration = !(live && listing.owner_declared);
  const canPublish = r.ready && digits(form.price) !== null && (!needDeclaration || declared);

  const save = () => run(() => api.saveListing(id, body(form)), () => setSaved(true));
  const publish = () =>
    run(async () => {
      if (status !== "paused") await api.saveListing(id, body(form));
      return api.publishListing(id, declared);
    });
  const close = async () => {
    const question = status === "draft" ? "lst_delete_confirm" : "lst_close_confirm";
    if (!(await confirm(webApp, k(question)))) return;
    await run(() => api.closeListing(id));
  };

  const content = (
    <>
      {!embedded && (
        <>
          <h1 className="left">{t("lst_title")}</h1>
          <p className="hint left">{formatAddress(prop)}</p>
        </>
      )}

      {listing && !live && !fresh && (
        <section className="card">
          <div className="stat">
            <h2 style={{ margin: 0, flex: 1 }}>{t("ls_rent_month", { rent: listing.price_amount.toLocaleString("uk-UA") })}</h2>
            <ListingChip status={listing.status} />
          </div>
          <p className="hint small">{k(`lst_closed_${listing.status}`, { date: formatDate(listing.closed_at) })}</p>
          {!r.unit_busy && listing.status !== "blocked" && (
            <button className="button" onClick={() => setFresh(true)}>
              <Icon name="plus" size={20} />
              {t("lst_new")}
            </button>
          )}
        </section>
      )}

      {status && (
        <section className="card">
          <div className="stat">
            <h2 style={{ margin: 0, flex: 1 }}>{t("lst_state")}</h2>
            <ListingChip status={status} />
          </div>
          <p className="hint small">{k(`lst_hint_${status}`)}</p>
          {listing?.published_at && status !== "draft" && (
            <p className="hint small">{t("lst_published_at", { date: formatDate(listing.published_at) })}</p>
          )}
        </section>
      )}

      {listing && !embedded && <IncomingApplications propertyId={id} onOpen={onApplication} />}
      {listing && !embedded && <ShowingsBlock propertyId={id} webApp={webApp} />}

      {editing && status !== "active" && (
        <section className="card">
          <h2>{t("lst_ready_title")}</h2>
          <ul className="checklist">
            <Check ok={r.ownership_verified} text={t("lst_ready_ownership")} action={r.ownership_verified ? undefined : onOwnership} />
            <Check
              ok={r.profile_ok}
              text={t("lst_ready_profile", { percent: String(r.completeness_percent), min: String(r.completeness_min) })}
              action={r.profile_ok ? undefined : onProfile}
            />
            <Check ok={r.photos > 0} soft text={r.photos > 0 ? t("lst_ready_photos", { n: String(r.photos) }) : t("lst_ready_no_photos")} action={r.photos > 0 ? undefined : onProfile} />
            {r.unit_busy && <Check ok={false} text={t("lst_ready_busy")} />}
          </ul>
        </section>
      )}

      {editing && (
        <section className="card">
          <h2>{t("lst_terms")}</h2>
          <label className="field">
            {t("lst_price")}
            <input className="input" inputMode="numeric" value={form.price} maxLength={9} onChange={(e) => setForm({ ...form, price: e.target.value })} />
          </label>
          <div className="field">
            {t("lst_utilities")}
            <Chips<boolean>
              value={form.utilities}
              options={[false, true]}
              label={(b) => (b ? t("lst_utilities_in") : t("lst_utilities_out"))}
              onChange={(b) => setForm({ ...form, utilities: b })}
            />
          </div>
          <div className="pair">
            <label className="field">
              {t("lst_deposit")}
              <input className="input" inputMode="numeric" value={form.deposit} maxLength={9} onChange={(e) => setForm({ ...form, deposit: e.target.value })} />
            </label>
            <label className="field">
              {t("lst_term")}
              <input className="input" inputMode="numeric" value={form.term} maxLength={2} onChange={(e) => setForm({ ...form, term: e.target.value })} />
            </label>
          </div>
          <label className="field">
            {t("lst_from")}
            <input className="input" type="date" value={form.from} onChange={(e) => setForm({ ...form, from: e.target.value })} />
          </label>
          <p className="hint small">{t("lst_from_hint")}</p>
          <p className="hint small">{t("lst_profile_note")}</p>

          {needDeclaration && (
            <label className="check">
              <input type="checkbox" checked={declared} onChange={(e) => setDeclared(e.target.checked)} />
              {t("lst_declaration")}
            </label>
          )}

          {error && <p className="error">{error}</p>}
          {saved && <p className="hint small">{t("lst_saved")}</p>}

          <div className="buttons">
            {status !== "active" && (
              <button className="button" disabled={busy || !canPublish} onClick={publish}>
                <Icon name="send" size={20} />
                {status === "paused" ? t("lst_resume") : t("lst_publish")}
              </button>
            )}
            <button className={status === "active" ? "button" : "button secondary"} disabled={busy || digits(form.price) === null} onClick={save}>
              {status === "active" || status === "paused" ? t("lst_save_changes") : t("lst_save_draft")}
            </button>
            {status === "active" && (
              <button className="button secondary" disabled={busy} onClick={() => run(() => api.pauseListing(id))}>
                {t("lst_pause")}
              </button>
            )}
          </div>
          {status && (
            <button className="link danger" disabled={busy} onClick={close}>
              {status === "draft" ? t("lst_delete") : t("lst_close")}
            </button>
          )}
        </section>
      )}

      {!editing && error && <p className="error">{error}</p>}
      <MascotTip tone="calm">{t("lst_tip")}</MascotTip>
    </>
  );
  return embedded ? content : <Screen onBack={onBack}>{content}</Screen>;
}

function Check({ ok, text, soft = false, action }: { ok: boolean; text: string; soft?: boolean; action?: () => void }) {
  const mark = ok ? "ok" : soft ? "soft" : "no";
  return (
    <li className={`checklist-row is-${mark}`}>
      <span className="checklist-mark">{ok ? "✓" : soft ? "•" : "✕"}</span>
      <span className="checklist-text">{text}</span>
      {action && (
        <button className="link" onClick={action}>
          {t("lst_ready_fix")}
        </button>
      )}
    </li>
  );
}
