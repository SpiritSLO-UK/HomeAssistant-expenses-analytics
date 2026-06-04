# Documentation

Everything you need to run, configure, secure and troubleshoot **HA Finance
Intelligence**. Start with the [main README](../README.md) for what the app does
and how to run it; the guides below go deeper.

## Guides

| Guide | What's inside |
|-------|---------------|
| [Configuration reference](configuration.md) | Every setting — environment variables (`HAFI_*`), add-on options, and the in-app Settings — what each does and its default. |
| [Troubleshooting](troubleshooting.md) | Fixes for common install / import / OCR / MQTT / AI / unlock / FX problems. |
| [Rules](rules.md) | How auto-categorisation rules work: precedence, every condition and action, and worked examples. |
| [Privacy](privacy.md) | Local-first model, what happens when AI is enabled, and the honest limits of third-party AI guarantees. |
| [Security & isolation](security.md) | Threat model, the private `/data` volume, file permissions, AppArmor, encryption at rest, and access control. |
| [HTTPS / reverse proxy](reverse-proxy.md) | Serve the standalone app over TLS — terminate HTTPS in a Caddy/nginx/Traefik proxy in front of port 8099 (bundled Caddy compose profile). |
| [Architecture](architecture.md) | How the pieces fit together (diagrams) — backend, frontend, data model, request flow. |
| [Context](context.md) | The living design/context notes for the project. |

## Quick links

- **Run it standalone:** [README → Beta quick-run](../README.md)
- **What's changed:** [CHANGELOG](../CHANGELOG.md) (v0.9.0-beta + the post-beta wave)
- **Serve it over HTTPS:** [HTTPS / reverse proxy](reverse-proxy.md) (`docker-compose.tls.yml`)
- **Install on Home Assistant:** the [`addon/`](../addon/) folder (ingress panel on
  port 8099). HA packaging is being finished — see the main README.
- **Report a problem / request a feature:** open an issue on the
  [GitHub repo](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics).

> All processing is local by design. AI is **off by default**; nothing leaves your
> machine unless you explicitly enable it. See [Privacy](privacy.md).
