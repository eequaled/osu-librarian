# Auth outage audit — one-click relay linking returns 401 (2026-09-17/18)

## System map
Flow: local app (`web/auth.js`) → `POST {relay}/pair` → browser opens
`osu.ppy.sh/oauth/authorize?client_id=68736&redirect_uri=<relay>/auth/callback&state=<ticket>.<port>`
→ osu! → `GET {relay}/auth/callback?code=..&state=..` (`relay/server.py::_handle_callback`)
→ relay POSTs `https://osu.ppy.sh/oauth/token` (grant_type, client_id,
client_secret, code, redirect_uri, form-encoded) → on access_token, GET
`/api/v2/me/` → stores under ticket → 302 to
`http://127.0.0.1:<port>/api/auth/relay-return?ticket=..` → local app fetches
`GET {relay}/token?ticket=..` (one-time) → saves `~/.token.json` (0600).

Code: `relay/server.py` (exchange `_post`, callback `_handle_callback`),
`src/server.py` (`/api/auth/relay-return`, `/api/auth/status`),
`web/auth.js` (dialog + polling). Secrets: osu! app secret lives in (a) Render
env `OSU_CLIENT_SECRET` on service `srv-dam6vcf40ujc73a83kug`
(`osu-librarian-relay.onrender.com`), (b) `~/osu-librarian/settings.json`
(BYO flow). NEVER in repo. Relay never logs codes/tokens/secrets; request
logs omit query strings.

## Timeline (UTC+2, server times)
- Relay built, deployed to Render free (frankfurt), `/health`+`/pair` verified.
- User authorized on osu! → relay callback → **400 page:
  "token exchange failed: HTTP 401: Client authentication failed"**.
- Found cause #1: relay env held a hand-transcribed secret with 3 wrong chars
  (`...CnlQp8WUp0sZNzW...` vs real `...CnIQp8Wup0sZNzw...`). Fixed via Render
  API (PUT env-vars → 200), auto-redeploy; new deploy `dep-dam741...` live
  23:04. Live env value sha256 == sha256(user-supplied string) — confirmed.
  Local settings.json secret fixed too.
- User retried. Relay log shows `POST /pair` 23:15:24 and
  `GET /auth/callback` 23:15:40 → **401 again**. User reports "still happens".

## Decisive oracle test (no user action, secret never printed)
POSTed `https://osu.ppy.sh/oauth/token` with the live id/secret pair and a
DUMMY code from this machine. Result: **HTTP 400 `invalid_grant`**, NOT 401.
Meaning: as of audit time, the id/secret pair AUTHENTICATES. A wrong secret
would give 401 even with a dummy code. So the stored secret is (now) valid.

## Ranked hypotheses for the 23:15 401
1. **Stale Render instance served the callback.** Free tier sleeps/wakes;
   env-var redeploys replace instances, but a draining old instance (old
   secret) may briefly still serve. 11 min gap makes this unlikely — but NOT
   disproven (no version marker on error pages; can't tell which deploy
   answered). STRONGEST testable suspect.
2. **Double-submit / retry with consumed code.** A reused code gives
   `invalid_grant` (400), not 401 — ruled OUT for the observed message.
3. **User's pasted secret (chat) differs from the true secret.** Oracle says
   the stored string authenticates — ruled OUT, *provided the oracle and the
   relay use the same string* (both do: settings.json vs Render env, sha
   verified equal on Render side; settings.json side assumed same — VERIFY:
   sha256 both files).
4. **osu! quirk: redirect_uri/client mismatch at token time.** Registration
   demonstrably matches (osu! issued the code TO our callback). Token call
   sends identical redirect_uri. Unlikely.
5. **Trailing whitespace/encoding in Render env.** sha matched the exact chat
   string, but relay code does NOT `.strip()` the secret (client_id is
   stripped, secret isn't). If Render padded it, sha would differ — it
   didn't. Ruled out.
6. **osu! app edited/reset after copying.** If user pressed Reset/Update,
   old secret dies. The 23:15 attempt used the string from chat; if chat
   string is stale (reset happened after pasting), 401 fits PERFECTLY and
   the oracle (run later with settings.json — also updated from chat)...
   would ALSO 401. It didn't. Ruled out, unless settings.json was updated
   from a NEWER paste than Render env (check mtimes + sha both).

## Next diagnostics (in order — do not skip 1)
1. `sha256(settings.json secret) vs sha256(Render live env)` — confirm the
   two consumers hold byte-identical strings. Commands in §Commands.
2. Fresh end-to-end retry while tailing `render logs`: note EXACT time of the
   callback line; confirm no overlapping deploy (`render deploys list`) in
   that minute. If 401 again → stale-instance theory dead (fresh deploy +
   fresh attempt minutes apart, twice, is beyond coincidence).
3. Add a build/deploy marker to the relay (e.g. `X-Relay-Build` header or HTML
   comment with commit sha) so any future failure is attributable to a
   specific deploy. Then retry.
4. If 401 persists with proven-identical valid secrets: re-test the oracle
   WITH a real fresh code is impossible headless… instead create a SECOND
   osu! test app, point relay at it temporarily, retry. If second app works:
   original app is in a bad state (recreate it). If it also 401s: the bug is
   in OUR token request (capture raw bytes of the form body — WITHOUT secret
   — and compare against a known-good client e.g. curl with same fields).
5. Nuclear option: bypass relay for one attempt — register localhost callback
   on a second app and complete BYO flow locally. If THAT 401s too, it's the
   osu! account/app side, not our code at all.

## Commands
- Live env sha (no secret printed):
  `curl -s https://api.render.com/v1/services/srv-dam6vcf40ujc73a83kug/env-vars -H "Authorization: Bearer $(python3 -c "import os,re;print(re.search(r'key:\s*(\S+)',open(os.path.expanduser('~/.render/cli.yaml')).read()).group(1))")" | python3 -c "import json,sys,hashlib; [print(hashlib.sha256(e['envVar']['value'].encode()).hexdigest()[:16]) for e in json.load(sys.stdin) if e['envVar']['key']=='OSU_CLIENT_SECRET']"`
- Local sha: `python3 -c "import json,hashlib;print(hashlib.sha256(json.load(open('/home/eequaled/osu-librarian/settings.json'))['api']['client_secret'].encode()).hexdigest()[:16])"`
- Tail relay logs during retry: `render logs -r srv-dam6vcf40ujc73a83kug --tail 5` (poll; logs omit query strings by design)
- Deploys: `render deploys list srv-dam6vcf40ujc73a83kug -o json`
- Oracle re-test (dummy code; expect 400 invalid_grant, NEVER paste output with secret):
  form POST grant_type=authorization_code, client_id, client_secret (from settings file), code=deadbeef-…, redirect_uri=https://osu-librarian-relay.onrender.com/auth/callback

## Open questions for the user
- Exact time of the latest failed attempt (to correlate with logs/deploys)?
- Was Reset/Update pressed on the osu! app AFTER pasting the secret here?
- Is there more than one "librarian"-like app / did client_id 68736 get deleted+recreated?
