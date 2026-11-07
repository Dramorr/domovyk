// Перегляд PDF прямо в застосунку, сторінками (PDF.js). Без «чим відкрити файл»:
// у вебвʼю Telegram на телефонах PDF в iframe не відкривається, тож малюємо в canvas.
// Бібліотека важка — вантажимо її лише коли людина відкриває документ.
//
// Масштаб: щипок двома пальцями, подвійний дотик і кнопки «−» / «+» (не всім зручно
// розводити пальці). Після зміни масштабу сторінки перемальовуються в більшій
// роздільності — текст лишається чітким, а не розмитим.

import { useCallback, useEffect, useRef, useState } from "react";

import { t } from "../i18n";

type PdfJs = typeof import("pdfjs-dist/legacy/build/pdf.mjs");
type PdfDoc = Awaited<ReturnType<PdfJs["getDocument"]>["promise"]>;

const MIN_ZOOM = 1;
const MAX_ZOOM = 4;
const STEP = 0.5;
const MAX_CANVAS_PX = 4096; // межа ширини canvas (памʼять телефона)

let pdfjsPromise: Promise<PdfJs> | null = null;

function loadPdfJs(): Promise<PdfJs> {
  pdfjsPromise ??= (async () => {
    const lib = await import("pdfjs-dist/legacy/build/pdf.mjs");
    const worker = await import("pdfjs-dist/legacy/build/pdf.worker.min.mjs?url");
    lib.GlobalWorkerOptions.workerSrc = worker.default;
    return lib;
  })();
  return pdfjsPromise;
}

const clamp = (z: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

function distance(a: Touch, b: Touch): number {
  return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
}

export function PdfViewer({ data }: { data: ArrayBuffer }) {
  const scroller = useRef<HTMLDivElement>(null);
  const pages = useRef<HTMLDivElement>(null);
  const doc = useRef<PdfDoc | null>(null);
  const zoomRef = useRef(1);
  const renderGen = useRef(0);
  const [zoom, setZoom] = useState(1);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  /** Намалювати всі сторінки під поточну ширину й масштаб. */
  const renderAll = useCallback(async (z: number) => {
    const pdf = doc.current;
    const host = pages.current;
    const box = scroller.current;
    if (!pdf || !host || !box) return;
    const gen = ++renderGen.current;
    const width = box.clientWidth || 340;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    for (let n = 1; n <= pdf.numPages; n++) {
      if (gen !== renderGen.current) return; // уже почали новий рендер
      const page = await pdf.getPage(n);
      const base = page.getViewport({ scale: 1 });
      const target = Math.min(width * z * ratio, MAX_CANVAS_PX);
      const viewport = page.getViewport({ scale: target / base.width });
      let canvas = host.children[n - 1] as HTMLCanvasElement | undefined;
      if (!canvas) {
        canvas = document.createElement("canvas");
        canvas.className = "pdf-page";
        canvas.setAttribute("aria-label", t("pdf_page", { n: String(n), total: String(pdf.numPages) }));
        host.appendChild(canvas);
      }
      // Малюємо в окремий canvas і підміняємо: без мигання під час перемальовування.
      const next = document.createElement("canvas");
      next.width = Math.floor(viewport.width);
      next.height = Math.floor(viewport.height);
      const ctx = next.getContext("2d");
      if (!ctx) continue;
      await page.render({ canvasContext: ctx, viewport }).promise;
      if (gen !== renderGen.current) return;
      canvas.width = next.width;
      canvas.height = next.height;
      canvas.getContext("2d")?.drawImage(next, 0, 0);
      if (n === 1) setState("ready");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const gens = renderGen; // лічильник рендерів (не DOM-вузол)
    pages.current?.replaceChildren();
    setState("loading");
    zoomRef.current = 1;
    setZoom(1);
    (async () => {
      try {
        const lib = await loadPdfJs();
        // Копія буфера: PDF.js забирає його у воркер (transfer).
        const pdf = await lib.getDocument({ data: new Uint8Array(data.slice(0)) }).promise;
        if (cancelled) {
          await pdf.destroy();
          return;
        }
        doc.current = pdf;
        await renderAll(1);
      } catch {
        if (!cancelled) setState("error");
      }
    })();
    return () => {
      cancelled = true;
      gens.current++;
      void doc.current?.destroy();
      doc.current = null;
    };
  }, [data, renderAll]);

  /** Змінити масштаб, тримаючи на місці точку (cx, cy) у координатах області перегляду. */
  const applyZoom = useCallback((next: number, cx?: number, cy?: number) => {
    const box = scroller.current;
    const inner = pages.current;
    if (!box || !inner) return;
    const prev = zoomRef.current;
    const z = clamp(next);
    if (z === prev) return;
    const x = cx ?? box.clientWidth / 2;
    const y = cy ?? box.clientHeight / 2;
    const k = z / prev;
    const left = (box.scrollLeft + x) * k - x;
    const top = (box.scrollTop + y) * k - y;
    zoomRef.current = z;
    inner.style.width = `${z * 100}%`;
    box.scrollLeft = left;
    box.scrollTop = top;
  }, []);

  // Після того як масштаб «устоявся» — перемалювати чітко.
  useEffect(() => {
    if (state === "loading") return;
    const id = window.setTimeout(() => void renderAll(zoom), 200);
    return () => window.clearTimeout(id);
  }, [zoom, renderAll, state]);

  // Щипок двома пальцями й подвійний дотик. Слухачі не-пасивні: інакше не скасувати
  // системний жест (зум сторінки чи закриття Mini App свайпом).
  useEffect(() => {
    const box = scroller.current;
    if (!box) return;
    let pinch: { dist: number; zoom: number } | null = null;
    let lastTap = 0;

    const point = (a: Touch, b?: Touch) => {
      const r = box.getBoundingClientRect();
      const x = b ? (a.clientX + b.clientX) / 2 : a.clientX;
      const y = b ? (a.clientY + b.clientY) / 2 : a.clientY;
      return [x - r.left, y - r.top] as const;
    };

    const onStart = (e: TouchEvent) => {
      if (e.touches.length === 2) {
        pinch = { dist: distance(e.touches[0], e.touches[1]), zoom: zoomRef.current };
        e.preventDefault();
      } else if (e.touches.length === 1) {
        const now = Date.now();
        if (now - lastTap < 300) {
          const [x, y] = point(e.touches[0]);
          applyZoom(zoomRef.current > 1.5 ? 1 : 2.5, x, y);
          setZoom(zoomRef.current);
          e.preventDefault();
          lastTap = 0;
        } else {
          lastTap = now;
        }
      }
    };
    const onMove = (e: TouchEvent) => {
      if (!pinch || e.touches.length !== 2) return;
      e.preventDefault();
      const [x, y] = point(e.touches[0], e.touches[1]);
      applyZoom((pinch.zoom * distance(e.touches[0], e.touches[1])) / pinch.dist, x, y);
    };
    const onEnd = (e: TouchEvent) => {
      if (pinch && e.touches.length < 2) {
        pinch = null;
        setZoom(zoomRef.current);
      }
    };
    box.addEventListener("touchstart", onStart, { passive: false });
    box.addEventListener("touchmove", onMove, { passive: false });
    box.addEventListener("touchend", onEnd);
    box.addEventListener("touchcancel", onEnd);
    return () => {
      box.removeEventListener("touchstart", onStart);
      box.removeEventListener("touchmove", onMove);
      box.removeEventListener("touchend", onEnd);
      box.removeEventListener("touchcancel", onEnd);
    };
  }, [applyZoom]);

  const step = (delta: number) => {
    applyZoom(zoomRef.current + delta);
    setZoom(zoomRef.current);
  };

  return (
    <div className="pdf-viewer">
      {state === "loading" && <div className="skeleton pdf-skeleton" />}
      {state === "error" && <p className="error">{t("pdf_error")}</p>}
      <div ref={scroller} className="pdf-scroll" hidden={state === "error"}>
        <div ref={pages} className="pdf-pages" />
      </div>
      {state === "ready" && (
        <div className="pdf-zoom" role="group" aria-label={t("pdf_zoom")}>
          <button onClick={() => step(-STEP)} disabled={zoom <= MIN_ZOOM} aria-label={t("pdf_zoom_out")}>
            −
          </button>
          <button
            className="pdf-zoom-value"
            onClick={() => step(1 - zoomRef.current)}
            aria-label={t("pdf_zoom_reset")}
          >
            {Math.round(zoom * 100)}%
          </button>
          <button onClick={() => step(STEP)} disabled={zoom >= MAX_ZOOM} aria-label={t("pdf_zoom_in")}>
            +
          </button>
        </div>
      )}
    </div>
  );
}
