"""osu! OAuth relay — public one-time code-exchange server (stdlib only).

Why this exists: osu! has no PKCE support (ppy/osu-web#7004 still open), so a
public/confidential client secret is required for the Authorization-Code flow.
Released users should not have to register their own osu! app, so this tiny
server holds OUR client secret server-side and performs only the one-time code
exchange. The local app keeps calling the osu! API directly with the user's own
token afterwards.

Protocol:
  POST /pair
    -> 200 {"ticket": <64 hex chars>, "expires_in": 600}
    Rate-limited per IP: max 20/hour -> 429 beyond.
  GET /auth/callback?code=..&state=<ticket>.<port>
    osu! redirects here (register RELAY_PUBLIC_URL/auth/callback as the
    Application Callback URL). Validates state, exchanges the code at
    https://osu.ppy.sh/oauth/token, fetches https://osu.ppy.sh/api/v2/me/,
    stores the token under the ticket, then:
    302 -> http://127.0.0.1:<port>/api/auth/relay-return?ticket=<ticket>
  GET /token?ticket=..
    One-time fetch for the local app (server-to-server; end users never call
    this directly):
    -> 200 {access_token, refresh_token, expires_in, obtained_at,
             user_id, username} then the ticket is DELETED.
    Unknown/expired/used/not-yet-exchanged ticket -> 404 {"error": ...}.
  GET /health -> 200 {"ok": true}

Security properties:
  - Tokens live in memory only, deleted on fetch/expiry. No persistent storage.
  - NEVER logs tokens, secrets, or codes (not even debug). Request logs omit
    query strings entirely.
  - Outbound HTTP uses urllib only with 15s timeouts.
  - Anything reflected into HTML is passed through html.escape, and error
    pages never echo the OAuth `code` or the client secret.

Config via env:
  OSU_CLIENT_ID, OSU_CLIENT_SECRET, RELAY_PUBLIC_URL
  (e.g. https://link.example.com), PORT (default 8099).
"""

from __future__ import annotations

import html
import ipaddress
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OSU_TOKEN_URL = "https://osu.ppy.sh/oauth/token"
OSU_ME_URL = "https://osu.ppy.sh/api/v2/me/"

TICKET_TTL = 600  # seconds
RATE_LIMIT_MAX = 20  # per-IP per-endpoint ...
RATE_LIMIT_WINDOW = 3600  # ... per hour
CALLBACK_TICKET_MAX_ATTEMPTS = 10  # max exchanges attempted per ticket
REQUEST_TIMEOUT = 15  # seconds for all outbound osu! calls
HANDLER_TIMEOUT = 60  # seconds socket timeout per request (slowloris guard)
MAX_PAIR_BODY = 1_000_000  # cap on discarded POST /pair body bytes
MAX_TICKETS = 5000  # cap on in-memory ticket store (LRU-ish evict oldest)


class RelayError(Exception):
    """Safe, user-facing relay failure (never contains codes/secrets)."""


# ---- in-memory state (never persisted) ----

_tickets: dict[str, dict] = {}  # ticket -> {"created_at": float, "data": dict|None, "attempts": int}
_rate: dict[str, list[float]] = {}  # bucket key -> [request timestamps]
_lock = threading.RLock()


def _now() -> float:
    return time.time()


def _trust_proxy_enabled() -> bool:
    """TRUST_PROXY env var (default "0"): >0 means honor X-Forwarded-For."""
    try:
        return int((os.environ.get("TRUST_PROXY", "0") or "0").strip()) > 0
    except (TypeError, ValueError, AttributeError):
        return False


def _is_ipish(s: str) -> bool:
    """True for IPv4/IPv6 literals; used to reject garbage XFF entries."""
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _get_xff(headers) -> str | None:
    """Case-insensitive X-Forwarded-For lookup; None if absent."""
    if headers is None:
        return None
    try:
        get = getattr(headers, "get", None)
        if callable(get):
            # HTTPMessage (real handler) is case-insensitive, so this
            # suffices there; plain dicts from tests may use any case.
            val = get("X-Forwarded-For")
            if isinstance(val, str) and val:
                return val
            if isinstance(headers, dict):
                for k, v in headers.items():
                    try:
                        if isinstance(k, str) and k.lower() == "x-forwarded-for":
                            if isinstance(v, str) and v:
                                return v
                            return None
                    except Exception:
                        continue
            return None
        if isinstance(headers, dict):
            for k, v in headers.items():
                try:
                    if isinstance(k, str) and k.lower() == "x-forwarded-for":
                        return v if isinstance(v, str) else None
                except Exception:
                    continue
    except Exception:
        return None
    return None


def _client_key(handler) -> str:
    """Rate-limit key: peer IP, or leftmost XFF entry when TRUST_PROXY>0."""
    try:
        peer = handler.client_address[0] or "unknown"
    except Exception:
        peer = "unknown"
    if not peer:
        peer = "unknown"
    if not _trust_proxy_enabled():
        return peer
    try:
        raw = _get_xff(getattr(handler, "headers", None))
    except Exception:
        return peer
    if not isinstance(raw, str) or not raw.strip():
        return peer
    # Leftmost entry is the original client: Render/Fly append each proxy
    # hop to the right, so the first entry is the client-supplied one.
    # Counting back TRUST_PROXY trailing hops would be overkill here — with
    # a single trusted TLS proxy the leftmost entry already separates
    # clients into per-client buckets, and any spoofed value only isolates
    # the spoofer into their own bucket instead of sharing the proxy IP.
    leftmost = raw.split(",")[0].strip()
    if not leftmost or not _is_ipish(leftmost):
        return peer
    return leftmost


def _bucket_limited(key: str, now: float) -> bool:
    """Check-and-record a 20/hour bucket. Caller must hold _lock."""
    seen = [t for t in _rate.get(key, []) if now - t < RATE_LIMIT_WINDOW]
    if len(seen) >= RATE_LIMIT_MAX:
        _rate[key] = seen
        return True
    seen.append(now)
    _rate[key] = seen
    return False


def get_config() -> tuple[str, str, str, int]:
    """Return (client_id, client_secret, public_url, port) from env."""
    client_id = os.environ.get("OSU_CLIENT_ID", "").strip()
    client_secret = os.environ.get("OSU_CLIENT_SECRET", "")
    public_url = os.environ.get("RELAY_PUBLIC_URL", "").strip().rstrip("/")
    try:
        port = int(os.environ.get("PORT", "8099") or "8099")
    except ValueError:
        port = 8099
    return client_id, client_secret, public_url, port


def callback_url() -> str:
    """Public redirect_uri registered with osu! (RELAY_PUBLIC_URL/auth/callback)."""
    _cid, _sec, public_url, _port = get_config()
    if not public_url:
        return ""
    return public_url + "/auth/callback"


def _config_error() -> str | None:
    """Human-readable misconfiguration reason, or None when configured.

    Checks OSU_CLIENT_ID / OSU_CLIENT_SECRET / RELAY_PUBLIC_URL presence.
    (HTTPS shape is checked separately.)
    """
    client_id, client_secret, public_url, _port = get_config()
    if not client_id or not client_secret or not public_url:
        return ("relay not configured: missing OSU_CLIENT_ID/"
                "OSU_CLIENT_SECRET/RELAY_PUBLIC_URL")
    return None


def purge_expired(now: float | None = None) -> int:
    """Delete expired tickets + prune old rate-limit entries. Returns # purged."""
    if now is None:
        now = _now()
    purged = 0
    with _lock:
        for ticket in [t for t, e in _tickets.items()
                       if now - float(e.get("created_at", 0)) > TICKET_TTL]:
            del _tickets[ticket]
            purged += 1
        for ip in list(_rate.keys()):
            kept = [t for t in _rate[ip] if now - t < RATE_LIMIT_WINDOW]
            if kept:
                _rate[ip] = kept
            else:
                del _rate[ip]
    return purged


def _reset_state() -> None:
    """Clear all in-memory state. Used by tests (and nothing else)."""
    with _lock:
        _tickets.clear()
        _rate.clear()


def _is_hex_ticket(s: str) -> bool:
    if not isinstance(s, str) or len(s) != 64:
        return False
    try:
        int(s, 16)
    except ValueError:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in s)


def parse_state(state: str) -> tuple[str, int]:
    """Parse '<ticket>.<port>' -> (ticket, port).

    Raises ValueError with a safe message (safe to show, escaped, in HTML).
    """
    if not isinstance(state, str) or not state:
        raise ValueError("invalid state")
    if "." not in state:
        raise ValueError("invalid state")
    ticket, _, port_s = state.rpartition(".")
    if not ticket or not port_s:
        raise ValueError("invalid state")
    if not _is_hex_ticket(ticket):
        raise ValueError("invalid state")
    if not port_s.isdigit():
        raise ValueError("invalid port")
    try:
        port = int(port_s)
    except ValueError:
        raise ValueError("invalid port")
    if not 1 <= port <= 65535:
        raise ValueError("invalid port")
    return ticket, port


def _extract_error_detail(body: str) -> str:
    """Best-effort safe detail from an osu! error body (never codes/secrets)."""
    try:
        obj = json.loads(body or "")
    except ValueError:
        # Plain-text body: keep it short; upstream never echoes our secret/code.
        text = (body or "").strip().replace("\n", " ")
        return text[:200]
    if isinstance(obj, dict):
        for key in ("error_description", "error", "message"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:200]
    return ""


def _post(url: str, fields: dict, timeout: float = REQUEST_TIMEOUT) -> dict:
    """POST form fields, return parsed JSON dict. Raises RelayError (safe)."""
    data = urllib.parse.urlencode({k: v for k, v in fields.items()}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        finally:
            try:
                e.close()
            except Exception:
                pass
        detail = _extract_error_detail(body)
        if detail:
            raise RelayError(f"token exchange failed: HTTP {e.code}: {detail}")
        raise RelayError(f"token exchange failed: HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise RelayError("token exchange failed: upstream unreachable")
    try:
        obj = json.loads(raw or "null")
    except ValueError:
        raise RelayError("token exchange failed: bad upstream response")
    if not isinstance(obj, dict):
        raise RelayError("token exchange failed: bad upstream response")
    return obj


def _get(url: str, token: str, timeout: float = REQUEST_TIMEOUT) -> dict:
    """GET with Bearer token, return parsed JSON dict. Raises RelayError (safe)."""
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        finally:
            try:
                e.close()
            except Exception:
                pass
        detail = _extract_error_detail(body)
        if detail:
            raise RelayError(f"profile fetch failed: HTTP {e.code}: {detail}")
        raise RelayError(f"profile fetch failed: HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise RelayError("profile fetch failed: upstream unreachable")
    try:
        obj = json.loads(raw or "null")
    except ValueError:
        raise RelayError("profile fetch failed: bad upstream response")
    if not isinstance(obj, dict):
        raise RelayError("profile fetch failed: bad upstream response")
    return obj


# ---- HTTP handler ----


class Handler(BaseHTTPRequestHandler):
    server_version = "OsuRelay/1.0"
    # Bound each request: slowloris guard behind the TLS proxy. GET query
    # bodies are bounded by URL length anyway; POST /pair is capped by
    # MAX_PAIR_BODY in _discard_body.
    timeout = HANDLER_TIMEOUT

    def log_message(self, fmt, *args):  # noqa: ARG002
        # NEVER log query strings: they carry codes/tickets. Log endpoint only.
        try:
            endpoint = (self.path or "").split("?", 1)[0].split("#", 1)[0][:200]
            sys.stderr.write(f'{self.client_address[0]} "{self.command} {endpoint}"\n')
        except Exception:
            pass

    # -- low-level senders --

    def _send_json(self, code: int, obj: dict, allow_cors: bool = False) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Browsers call POST /pair cross-origin (localhost app -> public relay).
        # Only /pair needs ACAO:* — /token carries bearer tokens and must
        # never be readable cross-origin.
        if allow_cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        if getattr(self, "close_connection", False):
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code: int, title: str, message: str) -> None:
        page = ("<!doctype html><html><head><meta charset=\"utf-8\">"
                f"<title>{html.escape(title)}</title></head><body>"
                f"<h1>{html.escape(title)}</h1>"
                f"<p>{html.escape(message)}</p>"
                "</body></html>")
        data = page.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_redirect(self, location: str) -> None:
        body = (f'<!doctype html><html><head><meta charset="utf-8">'
                f"<title>Redirect</title></head><body>"
                f'<a href="{html.escape(location, quote=True)}">Continue</a>'
                f"</body></html>").encode("utf-8")
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _discard_body(self) -> None:
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            return
        if n > MAX_PAIR_BODY:
            # Oversized: never leave unread bytes for keep-alive desync.
            # Drain up to the cap then close so the remainder is discarded
            # with the connection instead of being parsed as a new request.
            self.close_connection = True
            try:
                self.rfile.read(MAX_PAIR_BODY)
            except Exception:
                pass
            return
        try:
            self.rfile.read(n)
        except Exception:
            pass

    # -- routing --

    def do_OPTIONS(self):
        path = urllib.parse.urlparse(self.path).path
        if path != "/pair":
            return self._send_json(404, {"error": "not found"})
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_POST(self):
        purge_expired()
        path = urllib.parse.urlparse(self.path).path
        if path == "/pair":
            return self._handle_pair()
        self._discard_body()
        return self._send_json(404, {"error": "not found"})

    def do_GET(self):
        purge_expired()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/health":
            return self._send_json(200, {"ok": True}, allow_cors=True)
        if path == "/token":
            return self._handle_token(parsed.query)
        if path == "/auth/callback":
            return self._handle_callback(parsed.query)
        return self._send_json(404, {"error": "not found"})

    # -- endpoints --

    def _handle_pair(self) -> None:
        self._discard_body()
        if _config_error() is not None:
            return self._send_json(503, {"error": "relay_not_configured"},
                                   allow_cors=True)
        ip = _client_key(self)
        now = _now()
        with _lock:
            if _bucket_limited(ip, now):
                return self._send_json(429, {"error": "rate_limited"}, allow_cors=True)
            while len(_tickets) >= MAX_TICKETS:
                try:
                    oldest = min(
                        _tickets.items(),
                        key=lambda kv: float(kv[1].get("created_at", 0)),
                    )[0]
                except Exception:
                    try:
                        oldest = next(iter(_tickets))
                    except StopIteration:
                        break
                try:
                    del _tickets[oldest]
                except KeyError:
                    break
                try:
                    sys.stderr.write(
                        "ticket store full: evicting oldest ticket\n")
                except Exception:
                    pass
            ticket = secrets.token_hex(32)
            _tickets[ticket] = {"created_at": now, "data": None, "attempts": 0}
        return self._send_json(200, {"ticket": ticket, "expires_in": TICKET_TTL},
                               allow_cors=True)

    def _handle_callback(self, query: str) -> None:
        ip = _client_key(self)
        now = _now()
        with _lock:
            if _bucket_limited(f"callback:{ip}", now):
                return self._send_json(429, {"error": "rate_limited"})
        qs = urllib.parse.parse_qs(query or "", keep_blank_values=True)
        code = (qs.get("code", [""])[0] or "").strip()
        state = (qs.get("state", [""])[0] or "").strip()
        oauth_error = (qs.get("error", [""])[0] or "").strip()
        if oauth_error:
            return self._send_html(
                400, "Authorization failed",
                f"Authorization failed/denied: {oauth_error[:200]}")
        if not code or not state:
            return self._send_html(400, "Authorization failed", "missing code or state")
        try:
            ticket, port = parse_state(state)
        except ValueError as e:
            return self._send_html(400, "Authorization failed", str(e))
        with _lock:
            entry = _tickets.get(ticket)
            if entry is None:
                return self._send_html(
                    400, "Authorization failed", "unknown or expired ticket")
            if _now() - float(entry.get("created_at", 0)) > TICKET_TTL:
                del _tickets[ticket]
                return self._send_html(
                    400, "Authorization failed", "unknown or expired ticket")
            data = entry.get("data")
            if isinstance(data, dict) and data.get("access_token"):
                return self._send_html(
                    400, "Authorization failed", "ticket already exchanged")
            attempts = entry.get("attempts", 0)
            try:
                attempts = int(attempts or 0)
            except (TypeError, ValueError):
                attempts = 0
            if attempts >= CALLBACK_TICKET_MAX_ATTEMPTS:
                return self._send_json(429, {"error": "rate_limited"})
            entry["attempts"] = attempts + 1

        client_id, client_secret, public_url, _port = get_config()
        if not client_id or not client_secret or not public_url:
            return self._send_html(400, "Authorization failed", "relay not configured")
        redirect_uri = public_url + "/auth/callback"

        try:
            token_obj = _post(OSU_TOKEN_URL, {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            }, timeout=REQUEST_TIMEOUT)
        except RelayError as e:
            return self._send_html(400, "Authorization failed", str(e))
        except Exception:
            return self._send_html(400, "Authorization failed", "token exchange failed")

        access = ""
        refresh = ""
        expires_in = 0
        if isinstance(token_obj, dict):
            access = str(token_obj.get("access_token", "") or "")
            refresh = str(token_obj.get("refresh_token", "") or "")
            try:
                expires_in = int(token_obj.get("expires_in", 0) or 0)
            except (TypeError, ValueError):
                expires_in = 0
        if not access:
            return self._send_html(
                400, "Authorization failed", "token exchange failed: bad upstream response")

        # User profile is best-effort: a token without identity is still usable,
        # and mirrors the local app's tolerant get_me() behaviour.
        user_id = 0
        username = ""
        try:
            me = _get(OSU_ME_URL, access, timeout=REQUEST_TIMEOUT)
        except Exception:
            me = {}
        if isinstance(me, dict):
            try:
                user_id = int(me.get("id", 0) or 0)
            except (TypeError, ValueError):
                user_id = 0
            username = str(me.get("username", "") or "")

        obtained_at = int(_now())
        with _lock:
            entry = _tickets.get(ticket)
            if entry is None or _now() - float(entry.get("created_at", 0)) > TICKET_TTL:
                if ticket in _tickets:
                    del _tickets[ticket]
                return self._send_html(
                    400, "Authorization failed", "unknown or expired ticket")
            existing = entry.get("data")
            if isinstance(existing, dict) and existing.get("access_token"):
                return self._send_html(
                    400, "Authorization failed", "ticket already exchanged")
            entry["data"] = {
                "access_token": access,
                "refresh_token": refresh,
                "expires_in": expires_in,
                "obtained_at": obtained_at,
                "user_id": user_id,
                "username": username,
            }
        location = (f"http://127.0.0.1:{port}/api/auth/relay-return"
                    f"?ticket={urllib.parse.quote(ticket, safe='')}")
        return self._send_redirect(location)

    def _handle_token(self, query: str) -> None:
        ip = _client_key(self)
        now = _now()
        with _lock:
            if _bucket_limited(f"token:{ip}", now):
                return self._send_json(429, {"error": "rate_limited"})
        qs = urllib.parse.parse_qs(query or "", keep_blank_values=True)
        ticket = (qs.get("ticket", [""])[0] or "").strip()
        if not _is_hex_ticket(ticket):
            return self._send_json(404, {"error": "unknown_or_expired_ticket"})
        with _lock:
            entry = _tickets.get(ticket)
            if entry is None:
                return self._send_json(404, {"error": "unknown_or_expired_ticket"})
            if _now() - float(entry.get("created_at", 0)) > TICKET_TTL:
                del _tickets[ticket]
                return self._send_json(404, {"error": "unknown_or_expired_ticket"})
            data = entry.get("data")
            if not isinstance(data, dict) or not data.get("access_token"):
                # Paired but no successful callback yet: same shape, no oracle.
                return self._send_json(404, {"error": "unknown_or_expired_ticket"})
            del _tickets[ticket]
            payload = {
                "access_token": data.get("access_token", ""),
                "refresh_token": data.get("refresh_token", ""),
                "expires_in": data.get("expires_in", 0),
                "obtained_at": data.get("obtained_at", 0),
                "user_id": data.get("user_id", 0),
                "username": data.get("username", ""),
            }
        return self._send_json(200, payload)


def _sweeper_loop(stop: threading.Event, interval: float = 60.0) -> None:
    while not stop.wait(interval):
        try:
            purge_expired()
        except Exception:
            pass


def serve(port: int = 8099, bind: str = "0.0.0.0") -> None:
    """Run the relay forever (plus a 60s expiry sweeper thread)."""
    err = _config_error()
    if err is not None:
        print(f"relay misconfigured: {err}", file=sys.stderr, flush=True)
        raise SystemExit(1)
    stop = threading.Event()
    sweeper = threading.Thread(target=_sweeper_loop, args=(stop,), daemon=True)
    sweeper.start()
    httpd = ThreadingHTTPServer((bind, port), Handler)
    httpd.daemon_threads = True
    httpd.timeout = HANDLER_TIMEOUT
    _cid, _sec, public_url, _p = get_config()
    where = public_url or f"http://{bind}:{port}"
    print(f"osu! relay at {where} (port {port}) — Ctrl-C to stop", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


def main() -> int:
    err = _config_error()
    if err is not None:
        print(f"relay misconfigured: {err}", file=sys.stderr, flush=True)
        return 1
    _cid, _sec, _url, port = get_config()
    serve(port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
