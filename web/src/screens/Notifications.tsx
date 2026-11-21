// Стрічка сповіщень (дзвіночок у шапці): те саме, що приходило в бот, найновіше зверху.

import { useEffect, useState } from "react";

import { api, type NotificationItem } from "../api/client";
import { t } from "../i18n";
import { Icon } from "../icons";
import { errorText, formatDateTime, MascotTip, Screen } from "../ui";

export function NotificationsScreen({
  onBack,
  onOpen,
  onRead,
}: {
  onBack: () => void;
  /** Перехід зі сповіщення: той самий deep link, що й у кнопці бота. */
  onOpen: (start: string) => void;
  /** Стрічку відкрили — непрочитаних більше немає. */
  onRead: () => void;
}) {
  const [items, setItems] = useState<NotificationItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .notifications()
      .then((feed) => {
        setItems(feed.items);
        if (feed.unread > 0) api.notificationsRead().then(onRead).catch(() => undefined);
      })
      .catch((e: unknown) => setError(errorText(e)));
    // Позначаємо прочитаним один раз при відкритті.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Screen onBack={onBack} title={t("ntf_feed_title")}>
      {error && <p className="error">{error}</p>}
      {!items && !error && <div className="skeleton" />}
      {items && items.length === 0 && (
        <MascotTip>
          <p>{t("ntf_feed_empty")}</p>
        </MascotTip>
      )}
      {items && items.length > 0 && (
        <div className="feed">
          {items.map((n) => {
            const body = (
              <>
                <span className="feed-mark" aria-hidden />
                <span className="feed-text">
                  {n.text}
                  <span className="feed-time">{formatDateTime(n.at)}</span>
                </span>
                {n.start && <Icon name="chevron" size={20} />}
              </>
            );
            const cls = `feed-item${n.read ? "" : " unread"}`;
            return n.start ? (
              <button key={n.id} type="button" className={cls} onClick={() => onOpen(n.start as string)}>
                {body}
              </button>
            ) : (
              <div key={n.id} className={cls}>
                {body}
              </div>
            );
          })}
        </div>
      )}
      <p className="hint small">{t("ntf_feed_hint")}</p>
    </Screen>
  );
}
