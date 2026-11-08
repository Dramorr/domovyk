// Фото об'єкта (FR-PRP-9, ADR 0011): міні-слайдер, перегляд на весь екран і керування для власника.
// Файли — за авторизацією (initData), тому показуємо через blob-URL і тримаємо в кеші сесії.

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { createPortal } from "react-dom";

import { api, ApiFailure, type Photo } from "../api/client";
import { t, type TextKey } from "../i18n";
import { Icon } from "../icons";
import { confirm, type WebApp } from "../telegram";
import { errorText } from "../ui";

export const PHOTO_MAX = 15; // як PropertiesConfig.max_photos на бекенді

/** Звідки брати файли: власник — через об'єкт, учасники оренди — через оренду. */
export type PhotoSource = { kind: "property" | "lease"; id: string };

function photoPath(src: PhotoSource, photoId: string): string {
  const base = src.kind === "property" ? "properties" : "leases";
  return `/api/v1/${base}/${encodeURIComponent(src.id)}/photos/${encodeURIComponent(photoId)}`;
}

const urls = new Map<string, Promise<string>>();

function photoUrl(path: string): Promise<string> {
  let url = urls.get(path);
  if (!url) {
    url = api.photoFile(path).then(({ data }) => URL.createObjectURL(new Blob([data], { type: "image/jpeg" })));
    url.catch(() => urls.delete(path));
    urls.set(path, url);
  }
  return url;
}

/** Фото в рамці. `frame`: горизонтальне заповнює рамку, вертикальне — цілком на розмитому тлі з себе ж. */
function PhotoImage({ src, photo, mode }: { src: PhotoSource; photo: Photo; mode: "frame" | "cover" | "contain" }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const path = photoPath(src, photo.id);

  useEffect(() => {
    let alive = true;
    photoUrl(path)
      .then((u) => alive && setUrl(u))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [path]);

  if (!url) return <span className={`photo-ph${failed ? " failed" : ""}`} />;
  const portrait = photo.height > photo.width * 1.05;
  if (mode === "frame" && portrait) {
    return (
      <>
        <img className="photo-img photo-backdrop" src={url} alt="" aria-hidden draggable={false} />
        <img className="photo-img contain" src={url} alt="" draggable={false} />
      </>
    );
  }
  return <img className={`photo-img ${mode === "contain" ? "contain" : ""}`} src={url} alt="" draggable={false} />;
}

function useSlideIndex() {
  const ref = useRef<HTMLDivElement>(null);
  const [index, setIndex] = useState(0);
  const onScroll = () => {
    const el = ref.current;
    if (el && el.clientWidth) setIndex(Math.round(el.scrollLeft / el.clientWidth));
  };
  return { ref, index, onScroll };
}

function Dots({ count, index }: { count: number; index: number }) {
  if (count < 2) return null;
  return (
    <span className="photo-dots" aria-hidden>
      {Array.from({ length: count }, (_, i) => (
        <span key={i} className={i === index ? "on" : ""} />
      ))}
    </span>
  );
}

/** Міні-слайдер: гортання пальцем, крапки, лічильник; тап — на весь екран. `compact` — нижча рамка 16:9. */
export function PhotoSlider({ photos, source, compact = false }: { photos: Photo[]; source: PhotoSource; compact?: boolean }) {
  const { ref, index, onScroll } = useSlideIndex();
  const [full, setFull] = useState<number | null>(null);
  if (photos.length === 0) return null;
  return (
    <div className={`photo-slider${compact ? " compact" : ""}`}>
      <div className="photo-track" ref={ref} onScroll={onScroll}>
        {photos.map((p, i) => (
          <button
            key={p.id}
            type="button"
            className="photo-slide"
            aria-label={t("ph_open", { n: String(i + 1), total: String(photos.length) })}
            onClick={() => setFull(i)}
          >
            {Math.abs(i - index) <= 1 ? <PhotoImage src={source} photo={p} mode="frame" /> : <span className="photo-ph" />}
          </button>
        ))}
      </div>
      <Dots count={photos.length} index={index} />
      {photos.length > 1 && (
        <span className="photo-count">
          {index + 1}/{photos.length}
        </span>
      )}
      {full !== null && <PhotoViewer photos={photos} source={source} start={full} onClose={() => setFull(null)} />}
    </div>
  );
}

function PhotoViewer({
  photos,
  source,
  start,
  onClose,
}: {
  photos: Photo[];
  source: PhotoSource;
  start: number;
  onClose: () => void;
}) {
  const { ref, index, onScroll } = useSlideIndex();

  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollLeft = start * el.clientWidth;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [ref, start, onClose]);

  return createPortal(
    <div className="photo-viewer" role="dialog" aria-modal="true">
      <div className="photo-track" ref={ref} onScroll={onScroll}>
        {photos.map((p, i) => (
          <div key={p.id} className="photo-slide">
            {Math.abs(i - index) <= 1 ? <PhotoImage src={source} photo={p} mode="contain" /> : <span className="photo-ph" />}
          </div>
        ))}
      </div>
      <button type="button" className="photo-close" aria-label={t("ph_close")} onClick={onClose}>
        ✕
      </button>
      <Dots count={photos.length} index={index} />
      {photos.length > 1 && (
        <span className="photo-count">
          {index + 1}/{photos.length}
        </span>
      )}
    </div>,
    document.body,
  );
}

function photoError(e: unknown): string {
  if (e instanceof ApiFailure && e.kind === "domain" && e.field === "file" && e.reason) {
    return t(`ph_err_${e.reason}` as TextKey);
  }
  return errorText(e);
}

/** Керування фото об'єкта: додати (кілька одразу), зробити обкладинкою, видалити. */
export function PhotoManager({
  propertyId,
  photos,
  onChange,
  webApp,
}: {
  propertyId: string;
  photos: Photo[];
  onChange: (photos: Photo[]) => void;
  webApp: WebApp | null;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const source: PhotoSource = { kind: "property", id: propertyId };
  const room = PHOTO_MAX - photos.length;

  async function upload(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (!files.length) return;
    setError(null);
    const batch = files.slice(0, room);
    const errors: string[] = [];
    if (files.length > room) errors.push(t("ph_err_too_many"));
    setProgress({ done: 0, total: batch.length });
    for (const [i, file] of batch.entries()) {
      try {
        onChange(await api.addPhoto(propertyId, file));
      } catch (err) {
        errors.push(photoError(err));
      }
      setProgress({ done: i + 1, total: batch.length });
    }
    setProgress(null);
    if (errors.length) setError([...new Set(errors)].join(" "));
  }

  async function act(action: () => Promise<Photo[]>) {
    setBusy(true);
    setError(null);
    try {
      onChange(await action());
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <p className="hint small">{t("ph_hint", { max: String(PHOTO_MAX) })}</p>
      <div className="photo-grid">
        {photos.map((p, i) => (
          <div key={p.id} className="photo-tile">
            <PhotoImage src={source} photo={p} mode="cover" />
            {i === 0 && <span className="photo-badge">{t("ph_cover")}</span>}
            <div className="photo-tile-actions">
              {i > 0 && (
                <button
                  type="button"
                  disabled={busy}
                  aria-label={t("ph_make_cover")}
                  title={t("ph_make_cover")}
                  onClick={() => act(() => api.coverPhoto(propertyId, p.id))}
                >
                  ★
                </button>
              )}
              <button
                type="button"
                disabled={busy}
                aria-label={t("ph_delete")}
                title={t("ph_delete")}
                onClick={async () => {
                  if (await confirm(webApp, t("ph_delete_confirm"))) await act(() => api.deletePhoto(propertyId, p.id));
                }}
              >
                <Icon name="trash" size={16} />
              </button>
            </div>
          </div>
        ))}
        {room > 0 && (
          <button type="button" className="photo-tile photo-add" disabled={!!progress} onClick={() => input.current?.click()}>
            <Icon name="plus" size={26} />
            <span>{progress ? t("ph_uploading", { n: String(progress.done + 1), total: String(progress.total) }) : t("ph_add")}</span>
          </button>
        )}
      </div>
      <input
        ref={input}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        multiple
        hidden
        onChange={upload}
      />
      {error && <p className="error">{error}</p>}
    </div>
  );
}
