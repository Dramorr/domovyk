// Перегляд PDF прямо в застосунку, сторінками (PDF.js). Без «чим відкрити файл»:
// у вебвʼю Telegram на телефонах PDF в iframe не відкривається, тож малюємо в canvas.
// Бібліотека важка — вантажимо її лише коли людина відкриває документ.

import { useEffect, useRef, useState } from "react";

import { t } from "../i18n";

type PdfJs = typeof import("pdfjs-dist/legacy/build/pdf.mjs");

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

export function PdfViewer({ data }: { data: ArrayBuffer }) {
  const box = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let cancelled = false;
    const host = box.current;
    if (!host) return;
    host.replaceChildren();
    setState("loading");
    (async () => {
      try {
        const lib = await loadPdfJs();
        // Копія буфера: PDF.js забирає його у воркер (transfer).
        const doc = await lib.getDocument({ data: new Uint8Array(data.slice(0)) }).promise;
        const width = host.clientWidth || 340;
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        for (let n = 1; n <= doc.numPages && !cancelled; n++) {
          const page = await doc.getPage(n);
          const base = page.getViewport({ scale: 1 });
          const viewport = page.getViewport({ scale: (width / base.width) * ratio });
          const canvas = document.createElement("canvas");
          canvas.className = "pdf-page";
          canvas.width = Math.floor(viewport.width);
          canvas.height = Math.floor(viewport.height);
          canvas.setAttribute("aria-label", t("pdf_page", { n: String(n), total: String(doc.numPages) }));
          const ctx = canvas.getContext("2d");
          if (!ctx) continue;
          host.appendChild(canvas);
          await page.render({ canvasContext: ctx, viewport }).promise;
          if (n === 1 && !cancelled) setState("ready");
        }
        await doc.destroy();
      } catch {
        if (!cancelled) setState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [data]);

  return (
    <div className="pdf-viewer">
      {state === "loading" && <div className="skeleton pdf-skeleton" />}
      {state === "error" && <p className="error">{t("pdf_error")}</p>}
      <div ref={box} className="pdf-pages" />
    </div>
  );
}
