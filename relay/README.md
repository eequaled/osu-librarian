# osu! OAuth relay

Tiny **public** server that holds **our** osu! OAuth client secret so released
users can link their account with one click and never register their own osu!
app.

## Why it exists (no PKCE)

osu! does not support PKCE (`ppy/osu-web#7004` still open), so the OAuth
Authorization-Code flow needs a confidential `client_secret`. A purely local
app cannot keep a secret, and asking every user to register their own osu! app
is hostile. This relay performs **only the one-time code exchange**
server-side. Afterwards the local app calls the osu! API directly with the
user's own token — the relay is out of the loop.

## How it works

1. Local app: `POST https://<relay>/pair` → `{ticket, expires_in}`.
2. Local app opens the browser at
   `https://osu.ppy.sh/oauth/authorize?client_id=<ours>&redirect_uri=https://<relay>/auth/callback&response_type=code&scope=identify+public&state=<ticket>.<localport>`.
3. User approves → osu! redirects to
   `GET https://<relay>/auth/callback?code=..&state=<ticket>.<localport>`.
4. Relay exchanges the code at `https://osu.ppy.sh/oauth/token`, fetches
   `GET https://osu.ppy.sh/api/v2/me/` for `user_id`/`username`, stores the
   token **in memory under the ticket**, and
   `302` redirects to `http://127.0.0.1:<localport>/api/auth/relay-return?ticket=<ticket>`.
5. Local app (server-to-server): `GET https://<relay>/token?ticket=..` →
   `{access_token, refresh_token, expires_in, obtained_at, user_id, username}`.
   The ticket is **deleted on first fetch** (one-time). Unknown / expired /
   already-used tickets → `404 {error}`.
6. `GET /health` → `{ok: true}` (load-balancer / uptime checks).

## Env vars

| Var | Required | Default | Notes |
|---|---|---|---|
| `OSU_CLIENT_ID` | yes | — | Our osu! app's client id. |
| `OSU_CLIENT_SECRET` | yes | — | Our osu! app's secret. Never logged, never returned. |
| `RELAY_PUBLIC_URL` | yes | — | e.g. `https://link.example.com` (no trailing slash). `redirect_uri` is `<RELAY_PUBLIC_URL>/auth/callback`. |
| `PORT` | no | `8099` | Listen port. |

Run locally:

```bash
OSU_CLIENT_ID=… OSU_CLIENT_SECRET=… RELAY_PUBLIC_URL=https://link.example.com PORT=8099 \
  python3 relay/server.py
# or: python3 -m relay.server
```

Tests (stdlib only, no external network — osu! is mocked):

```bash
python3 -m unittest discover -s relay
```

## Registering the osu! app against it

1. Go to `https://osu.ppy.sh/home/account/edit` → OAuth section → new application.
2. Set **Application Callback URL** to exactly:
   `<RELAY_PUBLIC_URL>/auth/callback`
   (e.g. `https://link.example.com/auth/callback`).
3. Copy the issued `client_id` / `client_secret` into the relay's env
   (`OSU_CLIENT_ID` / `OSU_CLIENT_SECRET`).
4. Use the **same** `client_id` and `redirect_uri` when the local app builds
   the authorize URL in step 2 above, with `scope=identify public`.

## Deploy notes

- Any host with **Python 3 + HTTPS** works (no dependencies — stdlib only).
- **HTTPS is REQUIRED**: OAuth codes and bearer tokens transit this server.
  Terminate TLS at the platform (Fly.io / reverse proxy) or in front of it;
  never expose the relay over plain HTTP in production.
- The process is stateless apart from RAM (tickets + rate-limit buckets).
  Run one instance (or accept that tickets are per-instance if you scale).
- Health check: `GET /health`.
- Example with Docker (see `relay/Dockerfile`) or Fly.io (see `relay/fly.toml`):
  set the three secrets as env/secrets and deploy.

## Trust statement

- Tokens are held **in memory only** until the one-time fetch, then deleted.
  Expired tickets (TTL 600 s) are purged on every request and by a 60 s
  background sweeper. There is **no disk storage, no database, no cache file**.
- Tokens, client secrets, and OAuth codes are **never logged** (not even at
  debug level) — request logs omit query strings entirely — and error pages
  never echo the `code` or secret (reflected text is HTML-escaped).
- The relay never sees anything beyond the single exchange: after `/token` is
  fetched, all further osu! API calls happen directly between the user's
  machine and `osu.ppy.sh`.
