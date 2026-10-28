"""Modal.com port of the n8n workflow ``instantly-autoreply.json`` (fixed reply).

What it does:

1. Every 5 minutes, poll Gmail for *unread* emails from Instantly's reply
   notification address (``notifications@nt1.instantly.ai``).
2. Keep only the ones whose subject contains "may have sent a positive reply".
3. Extract the lead's email, then pull the conversation history and lead
   record from the Instantly API.
4. Render the fixed reply from ``reply-hautomai.md``.
5. Wait 180s, then send the reply (replying to the newest inbound message).
6. Mark the notification email as read before processing so it isn't handled
   twice (re-marked unread on failure so it retries).

No AI is involved: the reply body is a fixed template.

Deploy on Modal.com (see README.md for full instructions):

    modal deploy autoreply.py
"""

import json
import logging
import os
import re
import time

import modal

from reply_template import render_reply, plain_to_html

# ---------------------------------------------------------------------------
# Configuration (defaults mirror the original workflow)
# ---------------------------------------------------------------------------

INSTANTLY_BASE = "https://api.instantly.ai"
INSTANTLY_EMAILS_URL = f"{INSTANTLY_BASE}/api/v2/emails"
INSTANTLY_REPLY_URL = f"{INSTANTLY_BASE}/api/v2/emails/reply"
INSTANTLY_LEADS_URL = f"{INSTANTLY_BASE}/api/v2/leads"

NOTIFICATION_SENDER = "notifications@nt1.instantly.ai"
POSITIVE_REPLY_MARKER = "may have sent a positive reply"
WAIT_SECONDS = int(os.environ.get("WAIT_SECONDS", "180"))  # the n8n "Wait" node
CONVERSATION_LIMIT = 10

# All campaigns use the Hautomai copy; the Shoutt template is no longer routed.
DEFAULT_TEMPLATE_FILE = "reply-hautomai.md"

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _template_path(filename: str) -> str:
    """Resolve a template filename next to REPLY_TEMPLATE_PATH.

    Locally that is the repo dir (base ""); in the Modal image it is ``/``.
    """
    base = os.path.dirname(os.environ.get("REPLY_TEMPLATE_PATH", DEFAULT_TEMPLATE_FILE))
    return os.path.join(base, filename) if base else filename

logger = logging.getLogger("instantly-autoreply")
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Modal app / image / secrets
# ---------------------------------------------------------------------------

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "requests",
        "google-api-python-client",
        "google-auth",
    )
    .env({"REPLY_TEMPLATE_PATH": "/reply-hautomai.md"})
    .add_local_python_source("reply_template")
    .add_local_file("reply-hautomai.md", "/reply-hautomai.md")
    .add_local_file("reply-shoutt.md", "/reply-shoutt.md")
)

app = modal.App("instantly-autoreply")

# ---------------------------------------------------------------------------
# Instantly API helpers
# ---------------------------------------------------------------------------


def instantly_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['INSTANTLY_API_KEY']}",
        "Content-Type": "application/json",
    }


def get_conversation_history(search_term: str, limit: int = CONVERSATION_LIMIT) -> dict:
    import requests

    resp = requests.get(
        INSTANTLY_EMAILS_URL,
        headers=instantly_headers(),
        params={"limit": limit, "search": search_term},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_lead(lead_id: str) -> dict:
    import requests

    resp = requests.get(
        f"{INSTANTLY_LEADS_URL}/{lead_id}",
        headers=instantly_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def derive_first_name(search_term: str, subject: str | None = None) -> str:
    """Fallback first name when the lead record has no ``first_name``.

    Order of preference:
    1. The name embedded in the conversation subject (e.g. ``Re: Re: Mike``
       -> ``Mike``), since outreach subjects are personalized.
    2. The local part of an email-address search term, capitalized
       (``you@example.com`` -> ``Mle993302``).
    """
    if subject:
        cleaned = re.sub(r"^(re:\s*)+", "", subject.strip(), flags=re.IGNORECASE)
        cleaned = cleaned.strip().rstrip("?!.,;:")
        if cleaned:
            return cleaned.split()[-1].capitalize()
    local = search_term.split("@")[0] if "@" in search_term else search_term
    return local.capitalize()


def send_reply(payload: dict) -> dict:
    import requests

    resp = requests.post(
        INSTANTLY_REPLY_URL,
        headers=instantly_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Gmail helpers
# ---------------------------------------------------------------------------


def build_gmail_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token_json = os.environ.get("GMAIL_TOKEN_JSON")
    if token_json:
        info = json.loads(token_json)
    else:
        # Fallback to individual secret keys.
        info = {
            "client_id": os.environ["GMAIL_CLIENT_ID"],
            "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
            "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"],
            "token_uri": os.environ.get(
                "GMAIL_TOKEN_URI", "https://oauth2.googleapis.com/token"
            ),
        }

    creds = Credentials.from_authorized_user_info(info, GMAIL_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001 - never crash the cron on a dead token
            logger.error(
                "GMAIL TOKEN DEAD (%s). Re-authorize: cd instantly-autoreply "
                "&& .venv/bin/python gmail_token.py, then recreate the "
                "'instantly-autoreply' Modal secret with the new token.json.",
                exc,
            )
            raise

    return build("gmail", "v1", credentials=creds)


def list_unread_notification_ids(service) -> list:
    query = f"from:{NOTIFICATION_SENDER} is:unread"
    ids = []
    request = service.users().messages().list(userId="me", q=query, maxResults=100)
    while request is not None:
        response = request.execute()
        for msg in response.get("messages", []):
            if msg.get("id"):
                ids.append(msg["id"])
        request = service.users().messages().list_next(request, response)
    return ids


def get_message(service, msg_id: str) -> dict:
    # Only the Subject is used downstream. Fetch metadata only.
    message = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=msg_id,
            format="metadata",
            metadataHeaders=["Subject"],
        )
        .execute()
    )
    payload = message.get("payload", {})
    headers = payload.get("headers", [])

    def header(name: str) -> str:
        return next(
            (h["value"] for h in headers if h.get("name", "").lower() == name.lower()),
            "",
        )

    return {
        "id": msg_id,
        "subject": header("Subject"),
    }


def mark_read(service, msg_id: str) -> None:
    service.users().messages().modify(
        userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()


def mark_unread(service, msg_id: str) -> None:
    service.users().messages().modify(
        userId="me", id=msg_id, body={"addLabelIds": ["UNREAD"]}
    ).execute()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def process_notification(service, msg_id: str) -> None:
    msg = get_message(service, msg_id)
    subject = msg["subject"] or ""

    if POSITIVE_REPLY_MARKER not in subject:
        logger.info("Skipping %s (not a positive-reply notification)", msg_id)
        return

    lead_name = subject.split()[0] if subject.strip() else ""
    logger.info("Processing notification %s -> lead %r", msg_id, lead_name)

    history = get_conversation_history(lead_name)
    items = history.get("items") or []
    if not items:
        logger.warning("No conversation history for lead %r; aborting.", lead_name)
        return

    lead_id = next((it.get("lead_id") for it in items if it.get("lead_id")), None)
    lead = {}
    if lead_id:
        try:
            lead = get_lead(lead_id)
        except Exception:  # noqa: BLE001 - lead may have been deleted
            logger.warning("Lead %r not found; using fallback name.", lead_id)

    lead_email = (lead.get("email") or lead_name).lower()
    # The sending account comes from the conversation email item (v2 API); the
    # lead record no longer carries ``last_contacted_from``. EACCOUNT overrides.
    eaccount = (
        os.environ.get("EACCOUNT")
        or (lead.get("last_contacted_from") or "")
        or next((it.get("eaccount") for it in items if it.get("eaccount")), "")
    )

    # Never reply to our own message: if the newest message in the thread was
    # already sent by us, this lead has been handled (guards against retries).
    newest = items[0]
    if eaccount and (newest.get("from_address_email") or "").lower() == eaccount.lower():
        logger.info(
            "Newest message for lead %r is already ours; skipping duplicate reply.",
            lead_name,
        )
        return

    # Reply to the newest *inbound* message from the lead (not necessarily
    # items[0], which may be an outbound message).
    inbound = next(
        (
            it
            for it in items
            if (it.get("from_address_email") or "").lower() == lead_email
        ),
        None,
    )
    if inbound is None:
        logger.warning(
            "No inbound message from lead %r found in thread; skipping send.",
            lead_name,
        )
        return

    first_name = (
        (lead.get("first_name") or "").strip()
        or derive_first_name(lead_name, inbound.get("subject"))
    )

    logger.info(
        "Using reply template %s (campaign %s).",
        DEFAULT_TEMPLATE_FILE,
        lead.get("campaign") or "?",
    )
    reply_text = render_reply(
        first_name, template_path=_template_path(DEFAULT_TEMPLATE_FILE)
    )
    reply_html = plain_to_html(reply_text)

    reply_to_uuid = inbound.get("id") or ""
    subject_line = inbound.get("subject") or newest.get("subject") or ""

    # Faithful to the n8n "Wait" node, but longer so it feels less automated.
    time.sleep(WAIT_SECONDS)

    if not reply_to_uuid:
        logger.warning("Missing reply_to_uuid for lead %r; skipping send.", lead_name)
        return
    if "@" not in eaccount:
        logger.warning(
            "eaccount %r does not look like an email address for lead %r; "
            "skipping send (set the EACCOUNT env var to override).",
            eaccount,
            lead_name,
        )
        return

    payload = {
        "eaccount": eaccount,
        "reply_to_uuid": reply_to_uuid,
        "subject": subject_line,
        "body": {"text": reply_text, "html": reply_html},
    }
    logger.info("Sending reply to %r (reply_to_uuid=%s)", lead_name, reply_to_uuid)
    send_reply(payload)
    logger.info("Reply sent for lead %r.", lead_name)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("instantly-autoreply")],
    timeout=3600,
    schedule=modal.Cron("*/5 * * * *"),
)
@modal.concurrent(max_inputs=1)
def run() -> None:
    service = build_gmail_service()
    ids = list_unread_notification_ids(service)
    if not ids:
        return
    logger.info("Found %d unread Instantly notification(s).", len(ids))
    for msg_id in ids:
        # Claim the notification first so a later tick cannot pick it up while
        # we sleep before replying. On failure we put it back for a retry.
        try:
            mark_read(service, msg_id)
        except Exception:  # noqa: BLE001
            logger.exception("Could not mark %s as read before processing", msg_id)
        try:
            process_notification(service, msg_id)
        except Exception:  # noqa: BLE001 - log and retry on the next tick
            logger.exception(
                "Failed to process message %s; marking it unread to retry.", msg_id
            )
            try:
                mark_unread(service, msg_id)
            except Exception:  # noqa: BLE001
                logger.exception("Could not re-mark %s as unread", msg_id)


@app.local_entrypoint()
def main() -> None:
    """Run one pass locally: `modal run autoreply.py`."""
    run.local()
