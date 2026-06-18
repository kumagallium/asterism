#!/usr/bin/env python3
"""Asterism auth gate — a tiny, dependency-free session-cookie login.

Why this exists: browser-native HTTP Basic auth re-prompts constantly for an SPA
served over a self-signed cert (every refresh / client-side navigation). A session
cookie is sent automatically on every request — page loads, fetch/XHR, and SSE —
so the prompt disappears, and it works from anywhere (no VPN), on a bare IP, over
a self-signed cert.

Design goals (this is a security boundary, so keep it auditable):
  * STANDARD LIBRARY ONLY — no third-party deps = minimal attack surface.
  * The cookie is an HMAC-SHA256-signed, time-limited token. It carries no secret
    and cannot be forged without ASTERISM_GATE_SECRET. Verified in constant time.
  * The password is compared in constant time; a failed login sleeps briefly to
    slow brute force.
  * Cookie flags: HttpOnly (no JS access), Secure (HTTPS only), SameSite=Lax.

Caddy fronts this via `forward_auth` (it calls /__auth/verify on every request and
forwards the Cookie header). 2xx -> request proceeds; 302 -> browser goes to login.

Env:
  ASTERISM_GATE_PASSWORD  required — the shared login password.
  ASTERISM_GATE_SECRET    required — HMAC key for signing the session cookie.
  ASTERISM_GATE_TTL       optional — session lifetime in seconds (default 604800 = 7d).
  ASTERISM_GATE_PORT      optional — listen port (default 9000).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

COOKIE_NAME = "asterism_session"
PASSWORD = os.environ.get("ASTERISM_GATE_PASSWORD", "")
SECRET = os.environ.get("ASTERISM_GATE_SECRET", "").encode()
TTL = int(os.environ.get("ASTERISM_GATE_TTL", "604800"))
PORT = int(os.environ.get("ASTERISM_GATE_PORT", "9000"))

if not PASSWORD or not SECRET:
    raise SystemExit("ASTERISM_GATE_PASSWORD and ASTERISM_GATE_SECRET must be set")


def _sign(expiry: int) -> str:
    """Token = "<expiry>.<hex hmac>". Tamper-proof without the secret."""
    msg = str(expiry).encode()
    sig = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"


def _valid(token: str) -> bool:
    try:
        exp_str, sig = token.split(".", 1)
        expiry = int(exp_str)
    except (ValueError, AttributeError):
        return False
    expected = hmac.new(SECRET, str(expiry).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):  # constant-time
        return False
    return expiry > int(time.time())


def _cookie_from(headers) -> str | None:
    raw = headers.get("Cookie")
    if not raw:
        return None
    jar = SimpleCookie()
    jar.load(raw)
    morsel = jar.get(COOKIE_NAME)
    return morsel.value if morsel else None


_LOGIN_HTML = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Asterism — ログイン</title>
<style>
 body{{font-family:system-ui,sans-serif;background:#f3f5f2;display:grid;place-items:center;height:100vh;margin:0}}
 form{{background:#fff;padding:2rem 2.25rem;border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,.08);width:min(92vw,340px)}}
 h1{{font-size:1.1rem;margin:0 0 1rem;color:#2b3a2b}}
 input{{width:100%;box-sizing:border-box;padding:.6rem .7rem;border:1px solid #cdd6cd;border-radius:8px;font-size:1rem}}
 button{{width:100%;margin-top:.9rem;padding:.6rem;border:0;border-radius:8px;background:#3b6b3b;color:#fff;font-size:1rem;cursor:pointer}}
 .err{{color:#b00020;font-size:.85rem;margin-top:.6rem;min-height:1.1em}}
</style></head><body>
<form method="post" action="/__auth/login">
 <h1>Asterism にログイン</h1>
 <input type="password" name="password" placeholder="パスワード" autofocus autocomplete="current-password">
 <button type="submit">ログイン</button>
 <div class="err">{error}</div>
</form></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "asterism-authgate"

    def log_message(self, *a):  # quiet; Caddy already logs access
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _set_cookie_header(self, value: str, max_age: int) -> str:
        return (
            f"{COOKIE_NAME}={value}; Path=/; Max-Age={max_age}; "
            "HttpOnly; Secure; SameSite=Lax"
        )

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/__auth/health":
            return self._send(200, b"ok")
        if path == "/__auth/verify":
            tok = _cookie_from(self.headers)
            if tok and _valid(tok):
                return self._send(204)
            # Not authenticated -> tell Caddy/browser to go to the login page.
            return self._send(302, headers={"Location": "/__auth/login"})
        if path == "/__auth/logout":
            return self._send(
                302,
                headers={
                    "Location": "/__auth/login",
                    "Set-Cookie": self._set_cookie_header("", 0),
                },
            )
        if path == "/__auth/login":
            return self._send(
                200,
                _LOGIN_HTML.format(error="").encode(),
                {"Content-Type": "text/html; charset=utf-8"},
            )
        return self._send(404, b"not found")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/__auth/login":
            return self._send(404, b"not found")
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        password = (parse_qs(body).get("password", [""])[0])
        if hmac.compare_digest(password, PASSWORD):  # constant-time
            token = _sign(int(time.time()) + TTL)
            return self._send(
                303,
                headers={
                    "Location": "/",
                    "Set-Cookie": self._set_cookie_header(token, TTL),
                },
            )
        time.sleep(1.0)  # slow brute force
        return self._send(
            401,
            _LOGIN_HTML.format(error="パスワードが違います").encode(),
            {"Content-Type": "text/html; charset=utf-8"},
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
