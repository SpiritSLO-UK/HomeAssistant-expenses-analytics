# HTTPS / TLS (reverse proxy)

HA Finance Intelligence serves **plain HTTP** on port `8099` by design. That's
fine when you reach it over `localhost` on the same machine. The moment you reach
it from **another device** — a phone, a second laptop, anything across your
network — that traffic (including your login and the `X-HAFI-Session` token) is
unencrypted on the wire. The fix is to put a **reverse proxy in front of the app
that terminates TLS** and forwards plain HTTP to the app over a private network.

> **Why not build certificates into the app?** Certificate issuance, renewal, and
> trust are a solved problem that dedicated proxies (Caddy, nginx, Traefik) do far
> better than we could re-implement. Baking it in would add a fragile, security-
> sensitive surface for no benefit. So the app stays HTTP-only internally and you
> terminate TLS at the edge — the standard pattern for self-hosted apps.

> **Home Assistant users need none of this.** HA ingress (and Nabu Casa, or
> whatever reverse proxy already fronts your HA) terminates TLS for you, and the
> add-on serves plain HTTP internally on purpose. This guide is for the
> **standalone** Docker deployment.

## A note on the session token

The app keeps no session **cookie**. After unlocking, the browser stores a session
token in `localStorage` and sends it in a custom `X-HAFI-Session` header. There's
therefore no cookie to mark `Secure`/`HttpOnly` — but the token still travels over
the network on every request, so **TLS is what protects it in transit**. Serve the
app over HTTPS whenever it's reachable beyond `localhost`.

## Quick start — Caddy (bundled)

The repo ships a ready-to-use [`docker-compose.tls.yml`](../docker-compose.tls.yml)
and [`Caddyfile`](../Caddyfile). Caddy is the simplest option: automatic HTTPS with
near-zero config.

```bash
# A real domain pointed at this host → automatic Let's Encrypt certificate:
SITE_ADDRESS=finance.example.com docker compose -f docker-compose.tls.yml up -d --build
# then open https://finance.example.com
```

In this compose file the **app is not published to the host** (only `expose`d on the
internal Docker network); only Caddy publishes ports `80`/`443`, so the app is
reachable solely through the proxy.

### On your LAN, without a public domain

Public certificate authorities won't issue for a private name or IP, so use Caddy's
**internal CA** (it mints its own certificate). Edit the [`Caddyfile`](../Caddyfile)
to your LAN name and add `tls internal`:

```caddyfile
finance.local {
	tls internal
	reverse_proxy app:8099
	encode gzip
}
```

Point the name at the host (a router DNS entry, or a `hosts`-file line on each
device), bring it up, then **trust Caddy's root certificate once** so browsers stop
warning. It's stored in the `caddy_data` volume at
`/data/caddy/pki/authorities/local/root.crt`:

```bash
docker cp ha-finance-caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-local-ca.crt
# import caddy-local-ca.crt into your OS/browser trust store
```

(The default `SITE_ADDRESS=localhost` works out of the box with Caddy's local CA if
you only want to try HTTPS on the same machine.)

## Already running nginx / Traefik?

Any reverse proxy works — just forward to the app's port `8099` and let the proxy
hold the certificate. Minimal **nginx** server block:

```nginx
server {
    listen 443 ssl;
    server_name finance.example.com;

    ssl_certificate     /etc/letsencrypt/live/finance.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/finance.example.com/privkey.pem;
    add_header Strict-Transport-Security "max-age=31536000" always;

    location / {
        proxy_pass http://127.0.0.1:8099;   # or http://app:8099 on a shared network
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Traefik** users: route a TLS router to the `app` service on port `8099` and let
Traefik's resolver handle the certificate — the same shape as Caddy.

Serve the app at the **root** of its hostname (not under a sub-path) for the
standalone deployment; sub-path hosting is handled by HA ingress in HA mode.

## See also

- [Security & isolation](security.md) — the wider threat model and the private
  `/data` volume.
- [Configuration reference](configuration.md) — all `HAFI_*` settings.
