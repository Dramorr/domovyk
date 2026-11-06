// Чат оренди (FR-CHT-1, FR-CHT-2): живе в застосунку, бот лише сповіщає.
// Нові повідомлення — опитуванням раз на кілька секунд (WebSocket — коли знадобиться).

import { useCallback, useEffect, useRef, useState } from "react";

import { api, type Lease, type Message } from "../api/client";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import { errorText, MascotTip, Screen } from "../ui";

const POLL_MS = 4000;

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Kyiv" });
}

function systemText(m: Message): string {
  const params = (m.system_params ?? {}) as Record<string, unknown>;
  return t(`ch_sys_${m.system_code}` as TextKey, { name: String(params.name ?? "") });
}

export function Chat({ leaseId, onBack }: { leaseId: string; onBack: () => void }) {
  const [lease, setLease] = useState<Lease | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastId = useRef(0);
  const bottom = useRef<HTMLDivElement>(null);

  const me = lease?.members.find((m) => m.role === lease.my_role)?.user_id;
  const names = new Map(lease?.members.map((m) => [m.user_id, m.display_name]) ?? []);

  const append = useCallback(
    (fresh: Message[]) => {
      if (!fresh.length) return;
      setMessages((prev) => {
        const known = new Set(prev.map((m) => m.id));
        return [...prev, ...fresh.filter((m) => !known.has(m.id))];
      });
      lastId.current = Math.max(lastId.current, ...fresh.map((m) => m.id));
      void api.markRead(leaseId, lastId.current).catch(() => undefined);
    },
    [leaseId],
  );

  useEffect(() => {
    api.lease(leaseId).then(setLease).catch((e: unknown) => setError(errorText(e)));
    api
      .messages(leaseId)
      .then((page) => append(page.messages))
      .catch((e: unknown) => setError(errorText(e)));
    const timer = window.setInterval(() => {
      api
        .messages(leaseId, lastId.current)
        .then((page) => append(page.messages))
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [leaseId, append]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);

  async function send() {
    const body = text.trim();
    if (!body) return;
    setBusy(true);
    setError(null);
    try {
      append([await api.postMessage(leaseId, body)]);
      setText("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const userMessages = messages.filter((m) => !m.system_code);

  return (
    <Screen onBack={onBack} title={t("ch_title")}>
      {userMessages.length === 0 && (
        <MascotTip>
          <p>{t("ch_empty")}</p>
        </MascotTip>
      )}
      <div className="chat">
        {messages.map((m) =>
          m.system_code ? (
            <p key={m.id} className="msg-system">
              {systemText(m)}
            </p>
          ) : (
            <div key={m.id} className={`msg${m.sender_id === me ? " mine" : ""}`}>
              {m.sender_id !== me && <span className="msg-name">{names.get(m.sender_id ?? "") ?? ""}</span>}
              <span className="msg-body">{m.body}</span>
              <span className="msg-time">{time(m.created_at)}</span>
            </div>
          ),
        )}
        <div ref={bottom} />
      </div>
      {error && <p className="error">{error}</p>}
      <div className="composer">
        <textarea
          className="input"
          rows={1}
          maxLength={2000}
          value={text}
          placeholder={t("ch_placeholder")}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <button className="send" disabled={busy || !text.trim()} onClick={send} aria-label={t("ch_send")}>
          <Icon name="send" />
        </button>
      </div>
    </Screen>
  );
}
