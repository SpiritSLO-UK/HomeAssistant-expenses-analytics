# Documentation

Everything you need to run, configure, secure and troubleshoot **HA Finance
Intelligence**. Start with the [main README](../README.md) for what the app does
and how to run it; the guides below go deeper.

## Guides

| Guide | What's inside |
|-------|---------------|
| [Screenshots](screenshots.md) | A visual tour of the app on demo data - dashboard, transactions, receipts, search, budgets/investments, settings and the audit log. |
| [Standalone (docker-compose)](standalone.md) | Run it **without** Home Assistant on Docker - quick start, env-var config, the exposure/trust caveat, HTTPS and upgrades. |
| [Install on Home Assistant](ha-install.md) | Add the add-on repository, install the prebuilt image, configure options, ingress SSO, MQTT sensors and the energy-cost offset. |
| [Testing on Home Assistant](ha-testing.md) | End-to-end checklist for validating the add-on on a real HA (publish an RC image, make packages public, install, SSO, MQTT, energy, backups) before a release. |
| [Configuration reference](configuration.md) | Every setting - environment variables (`HAFI_*`), add-on options, and the in-app Settings - what each does and its default. |
| [Troubleshooting](troubleshooting.md) | Fixes for common install / import / OCR / MQTT / AI / unlock / FX problems. |
| [Rules](rules.md) | How auto-categorisation rules work: precedence, every condition and action, and worked examples. |
| [Privacy](privacy.md) | Local-first model, what happens when AI is enabled, and the honest limits of third-party AI guarantees. |
| [Security & isolation](security.md) | Threat model, the private `/data` volume, file permissions, AppArmor, encryption at rest, and access control. |
| [HTTPS / reverse proxy](reverse-proxy.md) | Serve the standalone app over TLS - terminate HTTPS in a Caddy/nginx/Traefik proxy in front of port 8099 (bundled Caddy compose profile). |
| [Architecture](architecture.md) ([HTML](architecture.html)) | How the pieces fit together (diagrams): system, request flow, import/categorise, the AI gateway & privacy gate (redaction choke-point), data model, and deployment. The HTML version is self-contained (inline SVG, renders offline). |
| [Context](context.md) | The living design/context notes for the project. |
| [Community intro post](community-intro.md) | A ready-to-post draft for the Home Assistant forum - what the app is, install, features, and a screenshots checklist. |

## Quick links

- **Run it standalone:** [Standalone (docker-compose)](standalone.md)
- **What's changed:** [CHANGELOG](../CHANGELOG.md) (current release: v1.0.2)
- **Serve it over HTTPS:** [HTTPS / reverse proxy](reverse-proxy.md) (`docker-compose.tls.yml`)
- **Install on Home Assistant:** [Install on Home Assistant](ha-install.md) -
  one-click repository add → Install (prebuilt image, ingress panel on port 8099).
- **Report a problem / request a feature:** open an issue on the
  [GitHub repo](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics).

> All processing is local by design. AI is **off by default**; nothing leaves your
> machine unless you explicitly enable it. See [Privacy](privacy.md).
