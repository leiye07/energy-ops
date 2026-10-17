from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs
import crypt, hmac, secrets, time, json, urllib.request, urllib.error

SESSIONS = {}
TOKEN = Path("/run/secrets/api_token").read_text().strip()
HASH = Path("/run/secrets/dashboard_password_hash").read_text().strip()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, body, ctype="text/html; charset=utf-8", headers=None):
        self.send_response(status)
        for k, v in {
            "Content-Type": ctype,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'",
            **(headers or {}),
        }.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode())

    def session(self):
        try:
            token = SimpleCookie(self.headers.get("Cookie", ""))[
                "energyops_session"
            ].value
        except (KeyError, ValueError):
            return None
        if SESSIONS.get(token, 0) < time.time():
            SESSIONS.pop(token, None)
            return None
        return token

    def origin_ok(self):
        return self.headers.get("Origin") == "http://" + self.headers.get("Host", "")

    def do_GET(self):
        if self.path == "/healthz":
            return self.send(200, '{"status":"ok"}', "application/json")
        if self.path == "/login":
            return self.send(200, Path("login.html").read_bytes())
        if not self.session():
            if self.path.startswith("/api/"):
                return self.send(
                    401, '{"detail":"Sign in required"}', "application/json"
                )
            return self.send(303, "", headers={"Location": "/login"})
        if self.path.startswith("/api/"):
            return self.proxy("GET")
        if self.path == "/":
            return self.send(200, Path("index.html").read_bytes())
        self.send(404, "Not found")

    def do_POST(self):
        if not self.origin_ok():
            return self.send(403, "Origin check failed")
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.send(400, "Invalid request")
        if n > 4096:
            return self.send(413, "Request too large")
        self.body = self.rfile.read(n)
        if self.path == "/login":
            form = parse_qs(self.body.decode())
            user = form.get("username", [""])[0]
            password = form.get("password", [""])[0]
            match = crypt.crypt(password, HASH)
            if user != "energyops" or not match or not hmac.compare_digest(match, HASH):
                time.sleep(1)
                return self.send(
                    401, '<p>Sign-in failed. <a href="/login">Try again</a>.</p>'
                )
            for expired, until in list(SESSIONS.items()):
                if until < time.time():
                    SESSIONS.pop(expired, None)
            token = secrets.token_urlsafe(32)
            SESSIONS[token] = time.time() + 28800
            return self.send(
                303,
                "",
                headers={
                    "Location": "/",
                    "Set-Cookie": "energyops_session="
                    + token
                    + "; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800",
                },
            )
        session = self.session()
        if not session:
            return self.send(401, '{"detail":"Sign in required"}', "application/json")
        if self.path == "/logout":
            SESSIONS.pop(session, None)
            return self.send(
                303,
                "",
                headers={
                    "Location": "/login",
                    "Set-Cookie": "energyops_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0",
                },
            )
        if self.path.startswith("/api/"):
            return self.proxy("POST")
        self.send(404, "Not found")

    def proxy(self, method):
        path = self.path.removeprefix("/api")
        allowed = [
            "/v1/status",
            "/v1/models",
            "/v1/observations",
            "/v1/forecasts/reference",
            "/v1/forecasts",
            "/v1/anomalies",
            "/v1/audit",
            "/v1/jobs",
            "/v1/rollback",
        ]
        if path not in allowed and not (
            path.startswith("/v1/models/") and path.endswith("/promote")
        ):
            return self.send(404, "Not found")
        req = urllib.request.Request(
            "http://runtime:8000" + path,
            data=getattr(self, "body", None) if method == "POST" else None,
            headers={
                "Authorization": "Bearer " + TOKEN,
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return self.send(r.status, r.read(), "application/json")
        except urllib.error.HTTPError as e:
            return self.send(e.code, e.read(), "application/json")
        except Exception:
            return self.send(
                503,
                '{"detail":"Runtime is temporarily unavailable"}',
                "application/json",
            )


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
