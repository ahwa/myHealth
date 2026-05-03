from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .config import APP_DIR


CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_REDIRECT_HOST = "localhost"
CODEX_SCOPE = "openid profile email offline_access"
AUTH_PATH = APP_DIR / "auth.json"


@dataclass
class CodexToken:
    access: str
    refresh: str
    expires: int
    account_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "CodexToken":
        return cls(
            access=data["access"],
            refresh=data["refresh"],
            expires=int(data["expires"]),
            account_id=data.get("accountId") or data.get("account_id"),
        )

    def to_dict(self) -> dict:
        return {
            "access": self.access,
            "refresh": self.refresh,
            "expires": self.expires,
            "accountId": self.account_id,
        }


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _json_post(url: str, payload: dict) -> dict:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            error_body = json.loads(exc.read().decode("utf-8"))
            msg = error_body.get("error", str(exc.code))
            desc = error_body.get("error_description", "")
            raise RuntimeError(f"OAuth request failed: {msg}" + (f" — {desc}" if desc else "")) from exc
        except (json.JSONDecodeError, AttributeError):
            raise RuntimeError(f"OAuth request failed with HTTP {exc.code}") from exc


def _account_id(access_token: str) -> str | None:
    parts = access_token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    return claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id") or claims.get(
        "sub"
    )


def _store_token(token: CodexToken, path: Path = AUTH_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_auth(path)
    existing["openai-codex"] = token.to_dict()
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def load_auth(path: Path = AUTH_PATH) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def get_codex_token(path: Path = AUTH_PATH) -> CodexToken | None:
    data = load_auth(path).get("openai-codex")
    return CodexToken.from_dict(data) if data else None


def clear_codex_token(path: Path = AUTH_PATH) -> None:
    data = load_auth(path)
    data.pop("openai-codex", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


class _CallbackHandler(BaseHTTPRequestHandler):
    server: "_CallbackServer"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        self.server.auth_code = params.get("code", [None])[0]
        self.server.auth_state = params.get("state", [None])[0]
        self.server.auth_error = params.get("error", [None])[0]
        self.server.auth_error_description = params.get("error_description", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Authentication received. Return to your terminal.")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


class _CallbackServer(HTTPServer):
    auth_code: str | None = None
    auth_state: str | None = None
    auth_error: str | None = None
    auth_error_description: str | None = None


def _check_state(received: str | None, expected: str) -> None:
    if received != expected:
        raise RuntimeError("OAuth state did not match; refusing token exchange.")


def _authorization_url(code_challenge: str, state: str, redirect_uri: str) -> str:
    params = {
        "response_type": "code",
        "client_id": CODEX_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": CODEX_SCOPE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{CODEX_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _code_from_pasted_value(value: str) -> tuple[str, str | None]:
    value = value.strip()
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urllib.parse.urlparse(value)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        if not code:
            raise RuntimeError("Pasted redirect URL did not include a code parameter.")
        return code, state
    return value, None


def login_openai_codex(timeout_seconds: int = 180, manual: bool = False) -> CodexToken:
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(24)

    auth_code: str | None = None
    auth_state: str | None = None
    redirect_uri: str | None = None

    if not manual:
        try:
            # Bind to port 0 so the OS picks a free port, matching the Codex CLI behaviour.
            server = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
            port = server.server_address[1]
            redirect_uri = f"http://{CODEX_REDIRECT_HOST}:{port}/auth/callback"
            auth_url = _authorization_url(challenge, state, redirect_uri)
            webbrowser.open(auth_url)
            server.timeout = 1
            deadline = time.time() + timeout_seconds
            while time.time() < deadline and not server.auth_code and not server.auth_error:
                server.handle_request()
            server.server_close()
            if server.auth_error:
                desc = server.auth_error_description
                msg = f"OAuth failed: {server.auth_error}"
                if desc:
                    msg += f" — {desc}"
                raise RuntimeError(msg)
            auth_code = server.auth_code
            auth_state = server.auth_state
        except OSError:
            print(
                "Warning: could not start local callback server; "
                "falling back to manual mode."
            )
            auth_code = None

    if not auth_code:
        if redirect_uri is None:
            redirect_uri = f"http://{CODEX_REDIRECT_HOST}:1455/auth/callback"
            auth_url = _authorization_url(challenge, state, redirect_uri)
            webbrowser.open(auth_url)
        print("Open this URL if your browser did not open:")
        print(auth_url)
        pasted = input("Paste the final redirect URL or authorization code: ")
        auth_code, auth_state = _code_from_pasted_value(pasted)

    _check_state(received=auth_state, expected=state)

    token_data = _json_post(
        CODEX_TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CODEX_CLIENT_ID,
            "code": auth_code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
    )
    token = CodexToken(
        access=token_data["access_token"],
        refresh=token_data["refresh_token"],
        expires=int(time.time()) + int(token_data.get("expires_in", 3600)),
        account_id=_account_id(token_data["access_token"]),
    )
    _store_token(token)
    return token


def refresh_openai_codex(token: CodexToken, path: Path = AUTH_PATH) -> CodexToken:
    token_data = _json_post(
        CODEX_TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "client_id": CODEX_CLIENT_ID,
            "refresh_token": token.refresh,
        },
    )
    refreshed = CodexToken(
        access=token_data["access_token"],
        refresh=token_data.get("refresh_token", token.refresh),
        expires=int(time.time()) + int(token_data.get("expires_in", 3600)),
        account_id=_account_id(token_data["access_token"]) or token.account_id,
    )
    _store_token(refreshed, path)
    return refreshed


def get_valid_codex_token(path: Path = AUTH_PATH) -> CodexToken:
    token = get_codex_token(path)
    if token is None:
        raise RuntimeError("No OpenAI Codex OAuth token. Run `myhealth auth login` first.")
    if token.expires <= int(time.time()) + 60:
        return refresh_openai_codex(token, path)
    return token
