import PagePlaceholder from "../components/PagePlaceholder";

export default function Settings() {
  return (
    <PagePlaceholder title="Settings" stage="later stages">
      <p className="muted">
        Will cover setup mode, privacy mode, currency, accounts, import
        profiles, AI providers, OCR, MQTT, HA sensors and backup (spec §25.12).
      </p>
    </PagePlaceholder>
  );
}
