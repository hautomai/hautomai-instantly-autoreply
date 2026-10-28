#!/usr/bin/env python3
"""Send a one-off reply to an active Instantly thread.

Unlike ``autoreply.py`` this is not scheduled: it takes a lead email and a
plain-text body file, finds the newest inbound message in that lead's thread,
and replies to it via ``POST /api/v2/emails/reply``.

Usage:
    python send_oneoff_reply.py prospect@example.com example-client.md
    python send_oneoff_reply.py prospect@example.com example-client.md --dry-run

The API key is read from ``.env``. Both ``INSTANTLY_API_KEY=<key>`` and the
legacy ``instantly <key>`` line formats are supported.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests

from reply_template import plain_to_html

INSTANTLY_EMAILS_URL = "https://api.instantly.ai/api/v2/emails"
INSTANTLY_REPLY_URL = "https://api.instantly.ai/api/v2/emails/reply"
INSTANTLY_LEADS_URL = "https://api.instantly.ai/api/v2/leads"

# Cloudflare rejects requests without a browser-like User-Agent (error 1010).
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def load_api_key() -> str:
    key = os.environ.get("INSTANTLY_API_KEY", "").strip()
    if key:
        return key

    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        sys.exit("No INSTANTLY_API_KEY in the environment and no .env file found.")
    with open(env_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                name, value = line.split("=", 1)
                if name.strip().upper() == "INSTANTLY_API_KEY":
                    return value.strip().strip('"').strip("'")
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "instantly":
                return parts[1].strip().strip('"').strip("'")
    sys.exit("Could not find an Instantly API key in .env.")


def headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }


def get_thread(api_key: str, email: str) -> list[dict]:
    resp = requests.get(
        INSTANTLY_EMAILS_URL,
        headers=headers(api_key),
        params={"limit": 50, "search": email},
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json().get("items") or []
    return [it for it in items if (it.get("from_address_email") or "").lower() == email.lower()]


def newest_inbound(items: list[dict]) -> dict:
    def ts(item: dict) -> datetime:
        value = item.get("timestamp_created") or ""
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    return max(items, key=ts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", help="Lead email address")
    parser.add_argument("body_file", help="Plain-text reply body")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the thread and print the payload without sending",
    )
    args = parser.parse_args()

    api_key = load_api_key()
    inbound_items = get_thread(api_key, args.email)
    if not inbound_items:
        sys.exit(f"No inbound messages found for {args.email}.")

    inbound = newest_inbound(inbound_items)
    lead_id = inbound.get("lead_id") or next(
        (it.get("lead_id") for it in inbound_items if it.get("lead_id")), None
    )
    eaccount = inbound.get("eaccount") or ""
    if not eaccount and lead_id:
        lead = requests.get(
            f"{INSTANTLY_LEADS_URL}/{lead_id}", headers=headers(api_key), timeout=30
        )
        if lead.ok:
            eaccount = lead.json().get("last_contacted_from") or ""

    with open(args.body_file, "r", encoding="utf-8") as f:
        body_text = f.read().strip()

    payload = {
        "eaccount": eaccount,
        "reply_to_uuid": inbound.get("id"),
        "subject": inbound.get("subject") or "",
        "body": {"text": body_text, "html": plain_to_html(body_text)},
    }

    print(f"Replying to {args.email} (inbound {inbound.get('id')} "
          f"from {inbound.get('timestamp_created')})")
    print(f"eaccount: {eaccount}")
    print(f"subject:  {payload['subject']}")
    print("-" * 60)
    print(body_text)
    print("-" * 60)

    if args.dry_run:
        print("Dry run: nothing sent.")
        return

    resp = requests.post(
        INSTANTLY_REPLY_URL, headers=headers(api_key), json=payload, timeout=30
    )
    print(f"HTTP {resp.status_code}")
    try:
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
    except ValueError:
        print(resp.text)
    resp.raise_for_status()


if __name__ == "__main__":
    main()
