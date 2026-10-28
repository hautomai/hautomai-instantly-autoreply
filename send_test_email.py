"""One-off helper: authorize Gmail via OAuth and send a test copy of the reply.

Two-step manual flow (no long-running local server, PKCE handled explicitly):

    python3 send_test_email.py url           # prints the Google authorization URL
    python3 send_test_email.py send <code>   # exchanges the code, saves token, sends email

On the `url` step, open the printed URL, sign in to the Gmail account, and
approve access. Google redirects to ``http://localhost:8080/?code=...&state=...``
(the page won't load since no server is running — that's fine). Copy the
``code`` value from the address bar and pass it to ``send``.
"""

import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import reply_template

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
TOKEN_PATH = "token.json"
CREDS_PATH = "credentials.json"
STATE_PATH = "oauth_state.json"
DEFAULT_TO = "you@example.com"
REDIRECT_URI = "http://localhost:8080/"


def _client_config() -> dict:
    with open(CREDS_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)["installed"]
    return cfg


def _make_verifier() -> str:
    # 43-128 chars from the unreserved set, per RFC 7636.
    return secrets.token_urlsafe(64)


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def cmd_url() -> None:
    cfg = _client_config()
    verifier = _make_verifier()
    challenge = _challenge(verifier)
    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": secrets.token_urlsafe(16),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    url = cfg["auth_uri"] + "?" + urllib.parse.urlencode(params)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"code_verifier": verifier}, f)
    print(url)


def _send(service, to: str, subject: str, body: str) -> dict:
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    msg.attach(MIMEText(reply_template.plain_to_html(body), "html", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()


def cmd_send(code: str) -> None:
    cfg = _client_config()
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        verifier = json.load(f)["code_verifier"]

    resp = requests.post(
        cfg["token_uri"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "code_verifier": verifier,
        },
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()

    creds = Credentials(
        token=token["access_token"],
        refresh_token=token.get("refresh_token"),
        token_uri=cfg["token_uri"],
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        scopes=SCOPES,
    )
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print("Saved credentials to", TOKEN_PATH)
    _send_test(creds)


def _send_test(creds: Credentials) -> None:
    body = reply_template.render_reply("Test")
    service = build("gmail", "v1", credentials=creds)
    result = _send(service, DEFAULT_TO, "Test reply (auto-reply preview)", body)
    print("SENT message id:", result.get("id"))
    profile = service.users().getProfile(userId="me").execute()
    print("FROM:", profile.get("emailAddress"))


def cmd_resend() -> None:
    """Re-send the test email using the already-saved token.json (no new code)."""
    if not os.path.exists(TOKEN_PATH):
        print(f"{TOKEN_PATH} not found; run the OAuth flow first.")
        sys.exit(1)
    from google.auth.transport.requests import Request

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    _send_test(creds)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "url":
        cmd_url()
    elif len(sys.argv) >= 3 and sys.argv[1] == "send":
        cmd_send(sys.argv[2])
    elif len(sys.argv) >= 2 and sys.argv[1] == "resend":
        cmd_resend()
    else:
        print(__doc__)
        sys.exit(1)
