def filter_customer_meetings(events, org_domain):
    """
    Filter calendar events to only include meetings with at least one
    attendee whose email domain is NOT the organization's domain.
    These are considered "customer calls."
    """
    customer_meetings = []
    org_domain_lower = org_domain.lower()

    for event in events:
        attendees = event.get("attendees", [])
        has_external = False
        external_attendees = []

        for attendee in attendees:
            email = attendee.get("emailAddress", {}).get("address", "")
            if email and not email.lower().endswith(f"@{org_domain_lower}"):
                has_external = True
                external_attendees.append({
                    "name": attendee.get("emailAddress", {}).get("name", ""),
                    "email": email,
                })

        if has_external:
            event["external_attendees"] = external_attendees
            customer_meetings.append(event)

    return customer_meetings


def filter_by_subject(events, keywords):
    """
    Filter events whose subject contains any of the given keywords.
    Case-insensitive matching.
    """
    if not keywords:
        return events

    filtered = []
    for event in events:
        subject = event.get("subject", "").lower()
        if any(kw.lower() in subject for kw in keywords):
            filtered.append(event)
    return filtered


def parse_vtt_transcript(vtt_text):
    """
    Parse a WebVTT transcript into structured entries.
    Returns a list of dicts with speaker, timestamp, and text.
    """
    entries = []
    lines = vtt_text.strip().split("\n")
    current_entry = {}

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if "-->" in line:
            current_entry["timestamp"] = line
            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip():
                text_line = lines[i].strip()
                # VTT speaker format: <v Speaker Name>text</v>
                if text_line.startswith("<v ") and ">" in text_line:
                    speaker_end = text_line.index(">")
                    speaker = text_line[3:speaker_end]
                    text = text_line[speaker_end + 1:].replace("</v>", "").strip()
                    current_entry["speaker"] = speaker
                    text_lines.append(text)
                else:
                    text_lines.append(text_line)
                i += 1

            current_entry["text"] = " ".join(text_lines)
            if current_entry.get("text"):
                entries.append(current_entry)
            current_entry = {}
        else:
            i += 1

    return entries


def _vtt_time_to_seconds(value: str) -> float | None:
    """'01:02:03.450' or '02:03.450' -> seconds."""
    parts = value.strip().replace(",", ".").split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return None


def transcript_view_lines(entries: list[dict]) -> list[dict]:
    """
    Parsed VTT entries -> lines for the timestamped transcript viewer:
    [{"start": seconds, "clock": "12:45", "speaker": str, "text": str}].
    Entries without a parsable cue start are skipped.
    """
    lines = []
    for entry in entries:
        start = _vtt_time_to_seconds((entry.get("timestamp") or "").split("-->")[0])
        if start is None or not entry.get("text"):
            continue
        whole = int(start)
        hours, rem = divmod(whole, 3600)
        minutes, seconds = divmod(rem, 60)
        clock = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
        lines.append({
            "start": round(start, 2),
            "clock": clock,
            "speaker": entry.get("speaker", "Unknown"),
            "text": entry["text"],
        })
    return lines


def transcript_to_readable(entries):
    """Convert parsed transcript entries into a readable text format."""
    lines = []
    current_speaker = None

    for entry in entries:
        speaker = entry.get("speaker", "Unknown")
        text = entry.get("text", "")

        if speaker != current_speaker:
            current_speaker = speaker
            lines.append(f"\n{speaker}:")

        lines.append(f"  {text}")

    return "\n".join(lines)
