import { useEffect, useId, useRef, useState } from "react";

const ZOOM_STEP = 0.25;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 4;
const clampZoom = (z: number): number => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));

/**
 * In-app preview of a receipt's original image/PDF — opens as a modal popup in the
 * same window instead of a new tab / download. Backed by /api/receipts/{id}/file
 * (served inline). PDFs render in an <iframe>; everything else as an <img>.
 * Native <dialog> gives the backdrop + Esc-to-close for free.
 */
export default function ReceiptPreview({
  url,
  filename,
  onClose,
}: Readonly<{
  url: string;
  filename?: string | null;
  onClose: () => void;
}>) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0); // degrees, always a multiple of 90
  useEffect(() => {
    ref.current?.showModal();
  }, []);

  const isPdf = (filename ?? "").toLowerCase().endsWith(".pdf");
  // Zoom/rotate act on the <img> via CSS transforms — they make no sense for the
  // PDF <iframe> (the browser's own viewer handles that), so guard the controls.
  const canTransform = !isPdf;
  const zoomPct = Math.round(zoom * 100);
  const isDefaultView = zoomPct === 100 && rotation === 0;
  const resetView = () => { setZoom(1); setRotation(0); };

  return (
    <dialog
      ref={ref}
      className="modal-dialog"
      aria-labelledby={titleId}
      // Native <dialog> handles Esc via the cancel event; preventDefault stops the
      // native close so React drives the unmount through onClose (single fire).
      // (Close via the ✕ button or Esc — no backdrop-click handler, which would
      // put a mouse/keyboard listener on a non-interactive element.)
      onCancel={(e) => { e.preventDefault(); onClose(); }}
    >
      <div className="card" style={{ margin: 0, maxWidth: "92vw" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
          <h2
            id={titleId}
            className="card__title"
            style={{ margin: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            {filename ?? "Receipt original"}
          </h2>
          <span style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
            {canTransform && (
              <span style={{ display: "flex", gap: 4, alignItems: "center" }} role="group" aria-label="Image view controls">
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  aria-label="Zoom out"
                  disabled={zoom <= ZOOM_MIN}
                  onClick={() => setZoom((z) => clampZoom(z - ZOOM_STEP))}
                >−</button>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  aria-label="Reset zoom and rotation"
                  disabled={isDefaultView}
                  onClick={resetView}
                >{zoomPct}%</button>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  aria-label="Zoom in"
                  disabled={zoom >= ZOOM_MAX}
                  onClick={() => setZoom((z) => clampZoom(z + ZOOM_STEP))}
                >+</button>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  aria-label="Rotate 90 degrees"
                  onClick={() => setRotation((r) => (r + 90) % 360)}
                >⟳</button>
              </span>
            )}
            <a className="link-btn" href={url} target="_blank" rel="noreferrer">Open in new tab ↗</a>
            <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>Close ✕</button>
          </span>
        </div>
        <div style={{ marginTop: 10 }}>
          {isPdf ? (
            <iframe src={url} title="Receipt PDF" sandbox="allow-same-origin" style={{ width: "82vw", height: "78vh", border: "none" }}>
              {/* Shown when the browser can't render the PDF inline (or blocks the frame). */}
              <p style={{ padding: 16 }}>
                This PDF can’t be previewed here.{" "}
                <a href={url} target="_blank" rel="noreferrer">Open in a new tab ↗</a> or{" "}
                <a href={url} download={filename ?? true}>download it</a>.
              </p>
            </iframe>
          ) : (
            // Zoomed image can exceed the box; let it scroll (basic pan) instead of
            // spilling out of the dialog. transform keeps layout cheap (no reflow).
            <div style={{ maxWidth: "88vw", maxHeight: "78vh", overflow: "auto", margin: "0 auto" }}>
              <img
                src={url}
                alt={filename ?? "Receipt"}
                style={{
                  maxWidth: "88vw",
                  maxHeight: "78vh",
                  display: "block",
                  margin: "0 auto",
                  objectFit: "contain",
                  transform: `scale(${zoom}) rotate(${rotation}deg)`,
                  transformOrigin: "center center",
                  transition: "transform 0.15s ease",
                }}
              />
            </div>
          )}
        </div>
      </div>
    </dialog>
  );
}
