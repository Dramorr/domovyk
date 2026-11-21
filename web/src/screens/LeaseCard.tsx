// Картка оренди (BOT_FLOWS §7): фото й ключові факти зверху, до активації — «шлях до
// заселення» слайдером із крапками (як у формі умов), розділи сторінки — у нижній панелі,
// налаштування сповіщень — дзвіночком у шапці.

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type TouchEvent } from "react";

import { api, type ContractState, type Invite, type Lease, type Photo } from "../api/client";
import { t, type TextKey } from "../i18n";
import { Icon, type IconName } from "../icons";
import { confirm, type WebApp } from "../telegram";
import { Avatar, Divider, errorText, formatDate, MascotTip, Screen, useStoredTab, type PageTab } from "../ui";
import { agreementSides, AgreementSheet } from "./AgreementBar";
import { ContractBlock } from "./Contract";
import { leaseAddress, LeaseStatusChip, TerminationBlock, termsLines } from "./Leases";
import { PaymentsBlock } from "./Payments";
import { PageHead } from "./PageHead";

const k = (key: string) => t(key as TextKey);

function money(n: number): string {
  return `${n.toLocaleString("uk-UA")} ₴`;
}

// ------------------------------------------------------------ вкладки

const TABS = ["overview", "terms", "people"] as const;

// ------------------------------------------------------------ шлях до заселення

type JourneyStep = { key: string; title: string; body: ReactNode };
type StepState = "done" | "now" | "next";

const SWIPE_PX = 48;
const STATE_ICON: Record<StepState, IconName> = { done: "check", now: "clock", next: "chevron" };

/** Неактивні слайди недоступні з клавіатури. Типи React 18 ще не знають `inert`. */
function inert(on: boolean): object {
  return on ? { inert: "" } : {};
}

/** Кроки оренди слайдером із крапками: відкривається на поточному, свайпом чи крапками
 *  можна глянути пройдені й наступні. Висота — під видимий слайд, без порожнечі. */
function Journey({ steps, current }: { steps: JourneyStep[]; current: number }) {
  const [at, setAt] = useState(current);
  useEffect(() => setAt(current), [current]);
  const slides = useRef<(HTMLDivElement | null)[]>([]);
  const [height, setHeight] = useState<number | undefined>(undefined);
  const touch = useRef<{ x: number; y: number } | null>(null);

  useLayoutEffect(() => {
    const el = slides.current[at];
    if (!el) return;
    const update = () => setHeight(el.offsetHeight);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [at]);

  function onTouchStart(e: TouchEvent) {
    const p = e.touches[0];
    touch.current = p ? { x: p.clientX, y: p.clientY } : null;
  }

  function onTouchEnd(e: TouchEvent) {
    const from = touch.current;
    const p = e.changedTouches[0];
    touch.current = null;
    if (!from || !p) return;
    const dx = p.clientX - from.x;
    // Вертикальна прокрутка сторінки — не свайп.
    if (Math.abs(dx) < SWIPE_PX || Math.abs(dx) < Math.abs(p.clientY - from.y)) return;
    if (dx > 0 && at > 0) setAt(at - 1);
    if (dx < 0 && at < steps.length - 1) setAt(at + 1);
  }

  const stateOf = (i: number): StepState => (i < current ? "done" : i === current ? "now" : "next");

  return (
    <section className="journey" aria-label={t("lj_title")}>
      <div className="journey-dots">
        {steps.map((s, i) => (
          <button
            key={s.key}
            className={`journey-dot is-${stateOf(i)}${i === at ? " active" : ""}`}
            aria-label={s.title}
            aria-current={i === at ? "step" : undefined}
            onClick={() => setAt(i)}
          />
        ))}
      </div>
      <div
        className="journey-viewport"
        style={height ? { height } : undefined}
        onTouchStart={onTouchStart}
        onTouchEnd={onTouchEnd}
      >
        <div className="journey-track" style={{ transform: `translateX(-${at * 100}%)` }}>
          {steps.map((s, i) => {
            const state = stateOf(i);
            return (
              <div
                key={s.key}
                ref={(el) => {
                  slides.current[i] = el;
                }}
                className={`journey-slide is-${state}`}
                aria-hidden={i !== at}
                {...inert(i !== at)}
              >
                <div className="journey-label">
                  <span>{t("lj_step", { n: String(i + 1), total: String(steps.length) })}</span>
                  <span className={`journey-state is-${state}`}>
                    <Icon name={STATE_ICON[state]} size={14} />
                    {k(`lj_state_${state}`)}
                  </span>
                </div>
                <h2>{s.title}</h2>
                {s.body}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function StepNote({ children }: { children: ReactNode }) {
  return <section className="card journey-note">{children}</section>;
}

// ------------------------------------------------------------ картка

export function LeaseCard({
  id,
  webApp,
  onBack,
  onChat,
  onEdit,
  onInvite,
  onPropose,
  onMeters,
}: {
  id: string;
  webApp: WebApp;
  onBack: () => void;
  onChat: () => void;
  onEdit: (lease: Lease) => void;
  onInvite: (invite: Invite) => void;
  onPropose: (lease: Lease) => void;
  onMeters: () => void;
}) {
  const [l, setL] = useState<Lease | null>(null);
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [contract, setContract] = useState<ContractState | null>(null);
  const [tab, setTab] = useStoredTab(`lease-tab:${id}`, TABS, "overview");
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
  const live = ["active", "ending", "ended"].includes(l.status);
  const beforeActive = ["draft", "awaiting_tenant", "awaiting_signatures"].includes(l.status);
  const iAmResponsible = l.my_role === "responsible";
  const responsible = l.members.find((m) => m.role === "responsible") ?? null;
  const needsMyAgreement =
    iAmResponsible && l.status === "awaiting_tenant" && l.agreement.tenant !== "agreed" && !l.proposal;
  const proposalForMe = l.my_role === "landlord" && !!l.proposal && l.status === "awaiting_tenant";
  const landlordMustAgree =
    l.my_role === "landlord" && l.status === "awaiting_tenant" && !l.proposal && l.agreement.landlord !== "agreed";
  const payText =
    l.pay.method === "cash" ? k("ls_pay_cash") : `${k(`ls_pay_${l.pay.method}`)} ${l.pay.masked ?? ""} · ${l.pay.recipient ?? ""}`;
  const inviteResponsible = () => run(async () => onInvite(await api.newInvite(l.id, "responsible")));

  // ---------------------------------------------------------- кроки до активації
  const current = !responsible ? 0 : l.status === "awaiting_signatures" ? 2 : 1;
  const sides = agreementSides(l, contract);
  const viewContract = responsible && (
    <button className="link" onClick={() => setSheet(true)}>
      {t("ls_view_contract")}
    </button>
  );

  const inviteNow = (
    <>
      <MascotTip>
        <p>{t("ls_wait_tenant_tip")}</p>
      </MascotTip>
      {l.pending_invites.map((i) => (
        <p key={i.role} className="hint small">
          {t("ls_pending_invite", { role: k(`ls_member_${i.role}`), date: formatDate(i.expires_at) })}
        </p>
      ))}
      {l.my_role === "landlord" && (
        <button className="button secondary" disabled={busy} onClick={inviteResponsible}>
          {t("ls_invite_again")}
        </button>
      )}
    </>
  );

  const termsNow = (
    <>
      {needsMyAgreement && (
        <section className="card accent">
          <h3>{t("ls_review_title")}</h3>
          <p className="hint">{t("ls_review_tip")}</p>
          {changes === null ? (
            <>
              <button className="button" disabled={busy} onClick={() => run(() => api.acceptTerms(l.id))}>
                <Icon name="check" size={20} />
                {t("ls_accept_terms")}
              </button>
              <button className="button secondary" onClick={() => onPropose(l)}>
                {t("ls_propose_changes")}
              </button>
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
      {proposalForMe && (
        <section className="card accent">
          <h3>{t("ls_proposal_title")}</h3>
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
          <h3>{t("ls_landlord_agree_title")}</h3>
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
      {l.my_role === "landlord" && !proposalForMe && !landlordMustAgree && (
        <MascotTip>
          <p>{t("ls_landlord_review_wait")}</p>
        </MascotTip>
      )}
      {iAmResponsible && (l.proposal || l.agreement.tenant === "agreed") && (
        <MascotTip>
          <p>{l.proposal ? t("ls_proposal_sent_tip") : t("ls_tenant_agreed_wait")}</p>
        </MascotTip>
      )}
      {l.my_role === "co_tenant" && (
        <MascotTip>
          <p>{t("lj_terms_co_tenant")}</p>
        </MascotTip>
      )}
      <section className="card journey-parties">
        <div className="unit">
          <span>{responsible?.display_name ?? t("ab_tenant")}</span>
          <span className={`hint small tone-${sides.tenant.terms}`}>{sides.tenant.label}</span>
        </div>
        <div className="unit">
          <span>{l.members.find((m) => m.role === "landlord")?.display_name ?? t("ab_landlord")}</span>
          <span className={`hint small tone-${sides.landlord.terms}`}>{sides.landlord.label}</span>
        </div>
        {viewContract}
      </section>
    </>
  );

  const contractNow =
    l.my_role === "co_tenant" ? (
      <MascotTip>
        <p>{t("ls_wait_contract_tip")}</p>
      </MascotTip>
    ) : (
      <ContractBlock
        key={`${l.id}:${l.status}`}
        leaseId={l.id}
        webApp={webApp}
        heading={false}
        onState={setContract}
        onActivated={() => {
          api.lease(l.id).then(setL).catch(() => undefined);
        }}
      />
    );

  const steps: JourneyStep[] = [
    {
      key: "invite",
      title: t("lj_invite"),
      body:
        current > 0 ? (
          <StepNote>
            <p>{iAmResponsible ? t("lj_invite_done_me") : t("lj_invite_done", { name: responsible?.display_name ?? "" })}</p>
          </StepNote>
        ) : (
          inviteNow
        ),
    },
    {
      key: "terms",
      title: t("lj_terms"),
      body:
        current > 1 ? (
          <StepNote>
            <p>{t("lj_terms_done")}</p>
            {viewContract}
          </StepNote>
        ) : current === 1 ? (
          termsNow
        ) : (
          <StepNote>
            <p className="hint">{t("lj_terms_next")}</p>
          </StepNote>
        ),
    },
    {
      key: "contract",
      title: t("lj_contract"),
      body:
        current === 2 ? (
          contractNow
        ) : (
          <StepNote>
            <p className="hint">{t("lj_contract_next")}</p>
          </StepNote>
        ),
    },
    {
      key: "movein",
      title: t("lj_movein"),
      body: (
        <StepNote>
          <p className="hint">{t("lj_movein_next")}</p>
        </StepNote>
      ),
    },
  ];

  // ---------------------------------------------------------- вкладка «Огляд»
  const overview = (
    <>
      {beforeActive && <Journey steps={steps} current={current} />}
      {l.status === "active" && l.my_role !== "co_tenant" && (
        <ContractBlock key={`${l.id}:${l.status}`} leaseId={l.id} webApp={webApp} onState={setContract} onActivated={() => undefined} />
      )}
      {live && (
        <PaymentsBlock
          leaseId={l.id}
          address={leaseAddress(l)}
          webApp={webApp}
          live={l.status === "active" || l.status === "ending"}
        />
      )}
      {(l.status === "active" || l.status === "ending") && l.my_role !== "co_tenant" && (
        <TerminationBlock l={l} webApp={webApp} busy={busy} run={run} />
      )}
      {l.status === "ended" && (
        <MascotTip tone="calm">
          <p>{t("ls_ended_tip")}</p>
        </MascotTip>
      )}
      {l.status === "cancelled" && (
        <MascotTip tone="calm">
          <p>{t("ls_cancelled_tip")}</p>
        </MascotTip>
      )}

      {l.my_role === "landlord" && l.can_edit && (
        <>
          <Divider />
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
        </>
      )}
      {iAmResponsible && ["awaiting_tenant", "awaiting_signatures"].includes(l.status) && (
        <>
          <Divider />
          <button
            className="link danger"
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
        </>
      )}
      {!open && (
        <>
          <Divider />
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
        </>
      )}
    </>
  );

  // ---------------------------------------------------------- вкладка «Умови»
  const terms = (
    <>
      <section className="card">
        <h2>{t("ls_sec_money")}</h2>
        <dl className="facts-list">
          <div>
            <dt>{t("lf_rent")}</dt>
            <dd>{money(l.rent_amount)}</dd>
          </div>
          <div>
            <dt>{t("lf_due")}</dt>
            <dd>{t("lf_due_value", { day: String(l.rent_due_day) })}</dd>
          </div>
          {!!l.deposit_amount && (
            <div>
              <dt>{t("lf_deposit")}</dt>
              <dd>{money(l.deposit_amount)}</dd>
            </div>
          )}
          <div>
            <dt>{t("lf_pay")}</dt>
            <dd>{payText}</dd>
          </div>
          <div>
            <dt>{t("lf_period")}</dt>
            <dd>{t("ls_period", { from: formatDate(l.start_date), to: formatDate(l.end_date) })}</dd>
          </div>
        </dl>
      </section>
      <section className="card">
        <h2>{t("ls_terms_title")}</h2>
        <ul className="plain">
          {termsLines(l.terms).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>
      {responsible && (
        <button className="button secondary" onClick={() => setSheet(true)}>
          <Icon name="doc" size={20} />
          {t("ls_view_contract")}
        </button>
      )}
      {l.can_edit && (
        <button className="button secondary" onClick={() => onEdit(l)}>
          <Icon name="edit" size={20} />
          {t("ls_edit")}
        </button>
      )}
    </>
  );

  // ---------------------------------------------------------- вкладка «Люди»
  const people = (
    <>
      <section className="card">
        {l.members.map((m) => (
          <div key={m.user_id} className="person">
            <Avatar userId={m.user_id} name={m.display_name} size={40} />
            <span className="person-body">
              <b>{m.display_name}</b>
              <span className="hint small">{k(`ls_member_${m.role}`)}</span>
            </span>
          </div>
        ))}
        {l.pending_invites.map((i) => (
          <p key={i.role} className="hint small">
            {t("ls_pending_invite", { role: k(`ls_member_${i.role}`), date: formatDate(i.expires_at) })}
          </p>
        ))}
      </section>
      {l.my_role === "landlord" && open && !responsible && (
        <button className="button secondary" disabled={busy} onClick={inviteResponsible}>
          {t("ls_invite_again")}
        </button>
      )}
      {l.can_invite_co_tenant && responsible && (
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => run(async () => onInvite(await api.newInvite(l.id, "co_tenant")))}
        >
          <Icon name="plus" size={20} />
          {t("ls_invite_co")}
        </button>
      )}
      {l.my_role === "co_tenant" && open && (
        <>
          <Divider />
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
        </>
      )}
    </>
  );

  const tabs: PageTab[] = [
    { key: "overview", icon: "home", label: t("ls_tab_overview"), active: tab === "overview", onClick: () => setTab("overview") },
    { key: "terms", icon: "doc", label: t("ls_tab_terms"), active: tab === "terms", onClick: () => setTab("terms") },
    { key: "people", icon: "users", label: t("ls_tab_people"), active: tab === "people", onClick: () => setTab("people") },
    { key: "chat", icon: "chat", label: t("ls_tab_chat"), count: l.unread, onClick: onChat },
    ...(live ? [{ key: "meters", icon: "meter" as const, label: t("mt_open"), onClick: onMeters }] : []),
  ];

  return (
    <Screen onBack={onBack} tabs={tabs}>
      <PageHead
        photos={photos}
        source={{ kind: "lease", id: l.id }}
        chip={<LeaseStatusChip status={l.status} />}
        aside={k(`ls_role_${l.my_role}`)}
        title={leaseAddress(l)}
        facts={[
          { label: t("lf_rent"), value: money(l.rent_amount) },
          { label: t("lf_due"), value: t("lf_due_value", { day: String(l.rent_due_day) }) },
          { label: t("lf_until"), value: formatDate(l.end_date) },
        ]}
      />

      {error && <p className="error">{error}</p>}
      {tab === "overview" && overview}
      {tab === "terms" && terms}
      {tab === "people" && people}

      {sheet && <AgreementSheet lease={l} contract={contract} termsLines={termsLines} webApp={webApp} onClose={() => setSheet(false)} />}
    </Screen>
  );
}
