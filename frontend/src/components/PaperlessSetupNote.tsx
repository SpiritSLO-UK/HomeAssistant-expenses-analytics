/** Shared Paperless-ngx setup guidance, so the Receipts page and Settings →
 *  Integrations show one consistent message (they used to drift). */
export default function PaperlessSetupNote() {
  return (
    <p className="muted" style={{ fontSize: "0.8rem" }}>
      Pull documents from your own <strong>Paperless-ngx</strong> into receipts — one-directional
      (we only ever request from Paperless, never the other way). Set the <strong>URL</strong> in
      Settings → Integrations; the <strong>API token</strong> is a secret, so set{" "}
      <code>HAFI_PAPERLESS_TOKEN</code> (and optionally <code>HAFI_PAPERLESS_URL</code>) in your{" "}
      <code>docker-compose.yml</code> or the add-on options, then restart. Leaving the URL blank falls
      back to <code>HAFI_PAPERLESS_URL</code>.
    </p>
  );
}
