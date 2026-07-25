"""One-time interactive Whoop OAuth2 authorization.

Run this once (and again only if you revoke access or lose the token):

    python -m wearables_mcp.authorize_whoop

It opens your browser to Whoop's consent screen, catches the redirect on a
temporary local HTTP server, exchanges the authorization code for an access +
refresh token, and stores the bundle encrypted on disk (Fernet key in the OS
keyring). Nothing is printed except status; secrets never touch stdout.
"""

from __future__ import annotations

import secrets
import sys
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from .config import load_config
from .token_store import TokenStore
from .whoop_client import WHOOP_AUTH_URL, WHOOP_SCOPES, WHOOP_TOKEN_URL


class _CallbackHandler(BaseHTTPRequestHandler):
    # Populated on the class so the server thread can hand the code back.
    auth_code: str | None = None
    auth_error: str | None = None
    expected_state: str | None = None

    def do_GET(self):  # noqa: N802 (http.server API)
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/callback"):
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        state = params.get("state", [None])[0]
        if state != _CallbackHandler.expected_state:
            _CallbackHandler.auth_error = "state mismatch (possible CSRF)"
        elif "error" in params:
            _CallbackHandler.auth_error = params.get(
                "error_description", params["error"]
            )[0]
        else:
            _CallbackHandler.auth_code = params.get("code", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = _CallbackHandler.auth_code is not None
        msg = (
            "Authorization complete. You can close this tab and return to the terminal."
            if ok
            else f"Authorization failed: {_CallbackHandler.auth_error}"
        )
        self.wfile.write(
            f"<html><body style='font-family:sans-serif'><h3>{msg}</h3></body></html>".encode()
        )

    def log_message(self, *args):  # silence default stderr access logging
        pass


def _run_callback_server(redirect_uri: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8080
    return host, port


def main() -> int:
    config = load_config()
    if not config.whoop_client_id or not config.whoop_client_secret:
        print(
            "ERROR: WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET must be set in .env.",
            file=sys.stderr,
        )
        return 1

    host, port = _run_callback_server(config.whoop_redirect_uri)
    state = secrets.token_urlsafe(16)  # Whoop requires state >= 8 chars
    _CallbackHandler.expected_state = state

    auth_params = {
        "client_id": config.whoop_client_id,
        "redirect_uri": config.whoop_redirect_uri,
        "response_type": "code",
        "scope": " ".join(WHOOP_SCOPES),
        "state": state,
    }
    auth_url = f"{WHOOP_AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    server = HTTPServer((host, port), _CallbackHandler)
    print(f"Opening browser for Whoop authorization on http://{host}:{port} ...")
    print(f"If it does not open, paste this URL:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # Serve requests until we get a code/error or time out (~5 min).
    server.timeout = 1
    deadline = time.time() + 300
    while (
        _CallbackHandler.auth_code is None
        and _CallbackHandler.auth_error is None
        and time.time() < deadline
    ):
        server.handle_request()

    if _CallbackHandler.auth_code is None:
        print(
            f"ERROR: did not receive an authorization code "
            f"({_CallbackHandler.auth_error or 'timed out'}).",
            file=sys.stderr,
        )
        return 1

    print("Exchanging authorization code for tokens ...")
    resp = requests.post(
        WHOOP_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": _CallbackHandler.auth_code,
            "client_id": config.whoop_client_id,
            "client_secret": config.whoop_client_secret,
            "redirect_uri": config.whoop_redirect_uri,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(
            f"ERROR: token exchange failed ({resp.status_code}): {resp.text[:400]}",
            file=sys.stderr,
        )
        return 1

    data = resp.json()
    if "refresh_token" not in data:
        print(
            "ERROR: no refresh_token returned. Make sure the `offline` scope is "
            "enabled on your Whoop app and granted on the consent screen.",
            file=sys.stderr,
        )
        return 1

    token = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": time.time() + int(data.get("expires_in", 3600)),
        "scope": data.get("scope", ""),
        "token_type": data.get("token_type", "bearer"),
    }
    TokenStore(config.token_path).save(token)
    print(
        f"Success. Encrypted token stored at {config.token_path}\n"
        "The MCP server can now refresh access automatically. You're done."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
