"""Fixed reply rendering (no AI).

Renders ``reply.md`` by substituting ``{{firstName}}``, ``{{accountSignature}}``
and ``{{timeSlots}}``, and replacing the legacy Liquid time-slot block with a
sentence whose weekday names are computed in Python (the original template had
them commented out).

``{{timeSlots}}`` expands to two concrete offers: the next business day at
17:00 and the following business day at 16:30, with weekends skipped, e.g.
``tomorrow 17:00 or Friday 16:30``.

Time-slot logic mirrors the Liquid block in ``reply.md``:

    today_number = ISO weekday (1=Monday .. 7=Sunday)
    day offsets chosen so the two slots always land on weekdays:
      Mon: tomorrow        + 2 days
      Tue: tomorrow        + 2 days
      Wed: tomorrow        + 5 days
      Thu: +4 days         + 5 days
      Fri: +3 days         + 4 days
      Sat: +2 days         + 3 days
      Sun: +1 day          + 2 days
"""

from __future__ import annotations

import os
import re
import zoneinfo
from datetime import datetime, timedelta
from html import escape

ACCOUNT_SIGNATURE = os.environ.get("ACCOUNT_SIGNATURE", "Hichem")
TZ_NAME = os.environ.get("TZ", "Europe/London")
REPLY_TEMPLATE_PATH = os.environ.get("REPLY_TEMPLATE_PATH", "reply.md")

# Markers that isolate the Liquid time-slot block inside reply.md.
LIQUID_START = "{% assign today_number"
SENTENCE_END = "5:00pm?"

# Placeholder for the two computed offers ("tomorrow 17:00 or Friday 16:30").
MEETING_SLOTS_TOKEN = "{{timeSlots}}"


def compute_time_slots(now: datetime) -> tuple[str, str]:
    """Return (day1, day2) for the "How's <day1> 1:00pm or <day2> 5:00pm?" line."""
    tz = zoneinfo.ZoneInfo(TZ_NAME)
    now = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    today = now.isoweekday()  # 1=Monday .. 7=Sunday

    if today == 1:      # Monday
        t2 = 2
    elif today == 2:    # Tuesday
        t2 = 2
    elif today == 3:    # Wednesday
        t2 = 5
    elif today == 4:    # Thursday
        t1, t2 = 4, 5
    elif today == 5:    # Friday
        t1, t2 = 3, 4
    elif today == 6:    # Saturday
        t1, t2 = 2, 3
    else:               # Sunday
        t1, t2 = 1, 2

    if today <= 3:
        day1 = "tomorrow"
    else:
        day1 = (now + timedelta(days=t1)).strftime("%A")

    day2 = (now + timedelta(days=t2)).strftime("%A")
    return day1, day2


def _next_business_day(day):
    """Return the first weekday strictly after ``day`` (skips Sat/Sun)."""
    candidate = day + timedelta(days=1)
    while candidate.isoweekday() > 5:
        candidate += timedelta(days=1)
    return candidate


def compute_meeting_slots(now: datetime | None = None) -> str:
    """Return the two offer slots, e.g. ``tomorrow 17:00 or Friday 16:30``.

    The first slot is the next business day at 17:00 ("tomorrow" when that is
    literally tomorrow), the second is the following business day at 16:30.
    Weekends are never offered.
    """
    tz = zoneinfo.ZoneInfo(TZ_NAME)
    now = now or datetime.now(tz)
    now = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    today = now.date()

    first = _next_business_day(today)
    second = _next_business_day(first)

    first_label = (
        "tomorrow"
        if first == today + timedelta(days=1)
        else first.strftime("%A")
    )
    return f"{first_label} 17:00 or {second.strftime('%A')} 16:30"


def render_reply(
    first_name: str,
    now: datetime | None = None,
    template_path: str | None = None,
) -> str:
    """Render the full reply body for the given lead first name."""
    with open(template_path or REPLY_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    body = template
    # Older templates carry a Liquid time-slot block that we replace with a
    # computed sentence. Newer templates may omit it entirely — inject only
    # when both markers are present so any reply.md renders fine.
    if LIQUID_START in body and SENTENCE_END in body:
        now = now or datetime.now(zoneinfo.ZoneInfo(TZ_NAME))
        day1, day2 = compute_time_slots(now)
        sentence = (
            "Or would you be open to a quick chat? "
            f"How's {day1} 1:00pm or {day2} 5:00pm?"
        )
        idx = body.index(LIQUID_START)
        tail = body[idx:]
        end = tail.index(SENTENCE_END) + len(SENTENCE_END)
        body = body[:idx] + sentence + tail[end:]

    body = body.replace(MEETING_SLOTS_TOKEN, compute_meeting_slots(now))
    body = body.replace("{{firstName}}", first_name or "")
    body = body.replace("{{accountSignature}}", ACCOUNT_SIGNATURE)
    return body


def plain_to_html(text: str) -> str:
    """Convert a plain-text body to a minimal HTML email body.

    - blank lines delimit paragraphs (``<p>``)
    - single newlines within a paragraph become ``<br>``
    - all text is HTML-escaped
    - the result is wrapped in a styled ``<div>`` (Arial 14px, line-height 1.5)
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = re.split(r"\n\s*\n", text)
    rendered = []
    for para in paragraphs:
        lines = [escape(line.rstrip(), quote=False) for line in para.split("\n")]
        lines = [line for line in lines if line.strip()]
        if not lines:
            continue
        rendered.append("<p>" + "<br>".join(lines) + "</p>")
    body = "\n".join(rendered)
    return (
        '<div style="font-family: Arial, Helvetica, sans-serif; '
        'font-size: 14px; line-height: 1.5;">\n'
        + body
        + "\n</div>"
    )
