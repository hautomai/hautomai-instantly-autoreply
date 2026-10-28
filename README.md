# Instantly Auto-Reply (Modal.com port)

A Python port of the n8n workflow `instantly-autoreply.json`, designed to run on
[Modal.com](https://modal.com) as a scheduled (every-5-minutes) job. It sends a
**fixed reply** (no AI), choosing the template by campaign.

## What it does

1. Polls Gmail every 5 minutes for **unread** emails from Instantly's reply
   notification address (`notifications@nt1.instantly.ai`).
2. Keeps only notifications whose subject contains
   `"may have sent a positive reply"`.
3. Reads the lead's email from the subject, then fetches the conversation
   history and lead record from the Instantly API.
4. Renders the fixed reply from `reply-hautomai.md`, waits 180s, then replies
   to the newest inbound message via Instantly's `POST /api/v2/emails/reply`.
5. Marks the notification email as read *before* processing (so a retry can't
   duplicate the reply) and re-marks it unread if processing fails.

## Files

| File                  | Purpose                                                          |
|-----------------------|------------------------------------------------------------------|
| `autoreply.py`        | The Modal app (cron job + pipeline + Instantly/Gmail helpers).   |
| `reply_template.py`   | Renders a template (first name, signature, time-slot logic).     |
| `reply-hautomai.md`   | Default reply template (permanent copy).                         |
| `reply-shoutt.md`     | Legacy Shoutt template (no longer routed).                       |
| `gmail_token.py`      | One-time helper to generate the Gmail OAuth token.               |
| `prompts.py`          | Old AI prompt (kept for a future AI-powered version; unused now).|

## Reply template

Every campaign uses `DEFAULT_TEMPLATE_FILE = "reply-hautomai.md"`; the old
per-campaign Shoutt routing was removed.

Placeholders substituted at send time:

- `{{firstName}}` → the lead's first name (from Instantly, falling back to the
  subject/email). Note the lowercase `f`: `{{FirstName}}` is **not** replaced.
- `{{accountSignature}}` → the `ACCOUNT_SIGNATURE` env var (default `Hichem`).
- `{{timeSlots}}` → two concrete offers computed in `TZ` (default
  `Europe/London`): the next business day at 17:00 and the following business
  day at 16:30, weekends skipped, e.g. `tomorrow 17:00 or Friday 16:30`.

If a template contains the legacy Liquid time-slot block, `reply_template.py`
still reimplements it (Mon–Wed: "tomorrow 1:00pm or `<weekday>` 5:00pm";
Thu–Sun: "`<weekday>` 1:00pm or `<weekday>` 5:00pm"). `reply-hautomai.md` no
longer uses it.

## Setup

### 1. Install & log in to Modal

```bash
pip install modal
modal token new   # opens a browser; authenticate with your Modal account
```

### 2. Get your secrets

- `INSTANTLY_API_KEY` — your Instantly API v2 key (the token **after** `Bearer `).
  In the original workflow this was hard-coded; **rotate it**, since it was
  committed in plaintext.
- `GMAIL_TOKEN_JSON` — a Gmail OAuth token (see below).

No OpenAI key is needed anymore.

### 3. Create the Gmail token

```bash
python gmail_token.py
```

This requires a `credentials.json` (Desktop-app OAuth client) from Google Cloud
Console with the Gmail API enabled. It prints a JSON blob — copy it.

### 4. Create the Modal secret

```bash
modal secret create instantly-autoreply \
  INSTANTLY_API_KEY="<your-instantly-key>" \
  GMAIL_TOKEN_JSON='<the-json-from-step-3>'
```

Optional overrides (defaults shown):

- `ACCOUNT_SIGNATURE` (default `Hichem`)
- `TZ` (default `Europe/London`)
- `WAIT_SECONDS` (default `180`)
- `EACCOUNT` (default: taken from the lead's `last_contacted_from`; set this to
  force a specific sending account)

To update the secret, use `modal secret create --force` (it overwrites in place
and keeps the same secret ID — delete/recreate breaks deployed apps).

### 5. Deploy

```bash
modal deploy autoreply.py
```

If the live app belongs to another Modal profile (currently `securha-n8n`),
prefix the command: `MODAL_PROFILE=securha-n8n modal deploy autoreply.py`.

The cron runs every 5 minutes. Watch the logs:

```bash
modal app logs instantly-autoreply
```

### Test one pass locally (without the cron)

```bash
modal run autoreply.py
```

## Notes & deviations from the original

- **No AI**: the reply is a fixed template; the OpenAI step was removed.
- **Time slots** are computed in Python if a template uses the legacy Liquid
  block.
- **Gmail** is read via the Gmail API (OAuth2) instead of n8n's Gmail trigger.
- **Secrets** moved to Modal secrets (the hard-coded Instantly key was removed
  from source).
- **No BCC**: replies are sent only to the lead.
- **Single template**: all campaigns use `reply-hautomai.md`.
- **No duplicate replies**: the notification is marked read before the wait, and
  a lead is skipped if the newest thread message is already ours. `reply_to_uuid`
  is always the newest *inbound* message, never our own.
- The reply is sent as both plain text (`body.text`) and HTML (`body.html`).
