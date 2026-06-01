/**
 * One-time "before you enable cloud AI" disclaimer (backlog #42). Shown the
 * first time a user switches to a cloud privacy mode, so enabling cloud AI is a
 * deliberate, informed choice. After acknowledgement it never shows again.
 */
export default function CloudAiDisclaimerDialog({
  onConfirm,
  onCancel,
}: {
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Before you enable cloud AI"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
        zIndex: 1000,
      }}
      onClick={onCancel}
    >
      <div className="card" style={{ maxWidth: 560, margin: 0 }} onClick={(e) => e.stopPropagation()}>
        <h2 className="card__title">☁️ Before you enable cloud AI</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          You're about to allow this app to send data to a cloud AI provider. Please read this once:
        </p>
        <ul style={{ lineHeight: 1.5, paddingLeft: 18 }}>
          <li>
            A <strong>minimal, redacted</strong> payload leaves your device — description, amount,
            currency and the list of candidate categories. Card/account numbers, sort codes, IBANs,
            postcodes and emails are masked first.
          </li>
          <li>
            <strong>You choose the endpoint.</strong> For a fully on-device setup, use a local LLM
            (<code>local_llm</code>) instead — then nothing leaves your device.
          </li>
          <li>
            <code>cloud_manual</code> shows you the exact payload and asks you to approve{" "}
            <em>each</em> request; <code>cloud_auto</code> sends automatically.
          </li>
          <li>Categories you mark <strong>never-cloud</strong> are never sent.</li>
          <li>Every call is recorded in the AI audit log.</li>
          <li>
            What the provider does with the data is governed by <strong>their</strong> policy — prefer a
            zero-retention endpoint or a local model. See the privacy docs for details.
          </li>
          <li>AI only ever <strong>suggests</strong> — you confirm, and it never overrides your choices.</li>
        </ul>
        <div className="form-row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
          <button className="btn btn--ghost" onClick={onCancel}>Cancel</button>
          <button className="btn" onClick={onConfirm}>I understand — enable cloud AI</button>
        </div>
      </div>
    </div>
  );
}
