import { useEffect, useRef, useState } from "react";

/**
 * Per-send warning before an *image* is sent to AI (Q3). Unlike transaction text,
 * an image can't be auto-redacted, so we confirm every time — until the user ticks
 * "Don't warn me again" (persisted per-device in prefs). Native <dialog> for the
 * backdrop / focus-trap / Esc-to-cancel.
 */
export default function AiImageWarningDialog({
  provider,
  onConfirm,
  onCancel,
}: Readonly<{
  provider?: string | null;
  onConfirm: (dontWarnAgain: boolean) => void;
  onCancel: () => void;
}>) {
  const ref = useRef<HTMLDialogElement>(null);
  const [dontWarn, setDontWarn] = useState(false);
  useEffect(() => {
    ref.current?.showModal();
  }, []);

  return (
    <dialog
      ref={ref}
      className="modal-dialog"
      aria-label="Send image to AI?"
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
    >
      <div className="card" style={{ maxWidth: 540, margin: 0 }}>
        <h2 className="card__title">✨ Send this image to AI?</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          The <strong>image</strong> will be sent to {provider ? <code>{provider}</code> : "your configured AI"} so it can
          read what OCR couldn't.
        </p>
        <ul style={{ lineHeight: 1.5, paddingLeft: 18 }}>
          <li>Unlike transaction text, an <strong>image can't be auto-redacted</strong> before it's sent.</li>
          <li>Receipts usually have card numbers masked already — but check before sending sensitive scans.</li>
          <li>For a fully on-device setup use a local LLM (<code>local_llm</code>). Every send is logged.</li>
        </ul>
        <label className="checkbox" style={{ marginTop: 4 }}>
          <input type="checkbox" checked={dontWarn} onChange={(e) => setDontWarn(e.target.checked)} /> Don't warn me again
        </label>
        <div className="form-row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
          {/* Focus the safe (Cancel) action by default — this gates sending an image to AI. */}
          <button className="btn btn--ghost" autoFocus onClick={onCancel}>Cancel</button>
          <button className="btn" onClick={() => onConfirm(dontWarn)}>Send to AI</button>
        </div>
      </div>
    </dialog>
  );
}
