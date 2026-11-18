// Чат оренди (FR-CHT-1, FR-CHT-2) і заявки (FR-APP-2): живе в застосунку, бот лише сповіщає.
// Нові повідомлення — опитуванням раз на кілька секунд (WebSocket — коли знадобиться).

import { useCallback, useEffect, useRef, useState } from "react";

import { api, type ChatSource, type Message } from "../api/client";
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

function isMine(sender: string | null | undefined, me: string | null): boolean {
  if (!me || !sender) return false;
  return me.startsWith("not:") ? sender !== me.slice(4) : sender === me;
}

type People = { me: string | null; names: Map<string, string> };

export function Chat({ source, onBack }: { source: ChatSource; onBack: () => void }) {
  const [people, setPeople] = useState<People>({ me: null, names: new Map() });
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastId = useRef(0);
  const bottom = useRef<HTMLDivElement>(null);

  const { me, names } = people;
  const key = `${source.kind}:${source.id}`;

  const append = useCallback(
    (fresh: Message[]) => {
      if (!fresh.length) return;
      setMessages((prev) => {
        const known = new Set(prev.map((m) => m.id));
        return [...prev, ...fresh.filter((m) => !known.has(m.id))];
      });
      lastId.current = Math.max(lastId.current, ...fresh.map((m) => m.id));
      void api.markRead(source, lastId.current).catch(() => undefined);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  );

  useEffect(() => {
    if (source.kind === "lease") {
      api
        .lease(source.id)
        .then((l) =>
          setPeople({
            me: l.members.find((m) => m.role === l.my_role)?.user_id ?? null,
            names: new Map(l.members.map((m) => [m.user_id, m.display_name])),
          }),
        )
        .catch((e: unknown) => setError(errorText(e)));
    } else {
      // У заявці двоє: «я» — той, хто не інша сторона.
      api
        .application(source.id)
        .then((c) => setPeople({ me: `not:${c.other.user_id}`, names: new Map([[c.other.user_id, c.other.display_name]]) }))
        .catch((e: unknown) => setError(errorText(e)));
    }
    api
      .messages(source)
      .then((page) => append(page.messages))
      .catch((e: unknown) => setError(errorText(e)));
    const timer = window.setInterval(() => {
      api
        .messages(source, lastId.current)
        .then((page) => append(page.messages))
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, append]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);

  async function send() {
    const body = text.trim();
    if (!body) return;
    setBusy(true);
    setError(null);
    try {
      append([await api.postMessage(source, body)]);
      setText("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const userMessages = messages.filter((m) => !m.system_code);

  return (
    <Screen onBack={onBack} title={source.kind === "lease" ? t("ch_title") : t("ap_chat_title")}>
      {userMessages.length === 0 && (
        <MascotTip>
          <p>{source.kind === "lease" ? t("ch_empty") : t("ch_empty_app")}</p>
        </MascotTip>
      )}
      <div className="chat">
        {messages.map((m) =>
          m.system_code ? (
            <p key={m.id} className="msg-system">
              {systemText(m)}
            </p>
          ) : (
            <div key={m.id} className={`msg${isMine(m.sender_id, me) ? " mine" : ""}`}>
              {!isMine(m.sender_id, me) && <span className="msg-name">{names.get(m.sender_id ?? "") ?? ""}</span>}
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
