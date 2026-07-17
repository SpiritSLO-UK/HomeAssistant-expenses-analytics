import { useEffect, useId, useRef } from "react";

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
  useEffect(() => {
    ref.current?.showModal();
  }, []);

  const isPdf = (filename ?? "").toLowerCase().endsWith(".pdf");

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
            <a className="link-btn" href={url} target="_blank" rel="noreferrer">Open in new tab ↗</a>
            <button className="btn btn--ghost btn--sm" onClick={onClose}>Close ✕</button>
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
            <img
              src={url}
              alt={filename ?? "Receipt"}
              style={{ maxWidth: "88vw", maxHeight: "78vh", display: "block", margin: "0 auto", objectFit: "contain" }}
            />
          )}
        </div>
      </div>
    </dialog>
  );
}
