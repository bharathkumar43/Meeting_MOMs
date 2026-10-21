"""
Free (non-AI) meeting insights that complement the AI conversation analysis:
talk-time and question metrics from the timestamped transcript, matching transcript
speakers to CloudFuze accounts, sharing one analysis between everyone who has the same
meeting, and the per-employee progress roll-up managers see.
"""
import json
import logging
import re
from datetime import datetime

from app import db
from app.models import MeetingAnalysis, MeetingRecord, User
from config import Config

logger = logging.getLogger(__name__)

_MAX_LINE_SECONDS = 30      # cap per cue when estimating speaking time (silence, pauses)
_WORDS_PER_SECOND = 2.5     # fallback duration for the last cue


# ── Names ────────────────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    """'Meghana Reddy (Guest)' -> 'meghana reddy'."""
    name = re.sub(r"\(.*?\)|\[.*?\]", " ", name or "")
    return " ".join(re.sub(r"[^\w\s]", " ", name.lower()).split())


def _same_person(a: str, b: str) -> bool:
    """Loose match between a transcript speaker and an account / model-reported name."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = set(na.split()), set(nb.split())
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    # 'Meghana' vs 'Meghana Reddy', 'Reddy Meghana' vs 'Meghana Reddy'
    return small <= big and (len(small) >= 2 or len(big) <= 2)


def org_users() -> list[tuple[str, str]]:
    """(name, email) of CloudFuze people who have signed in to the app."""
    domain = f"@{Config.ORG_DOMAIN.lower()}"
    return [(u.name, u.email.lower()) for u in User.query.all()
            if u.email and u.email.lower().endswith(domain) and u.name]


def attendee_name(attendee: dict) -> str:
    """Readable name for an external attendee: 'nesti.arene@cyonyx.io' -> 'Nesti Arene (cyonyx.io)'."""
    name = (attendee.get("name") or "").strip()
    email = (attendee.get("email") or (name if "@" in name else "")).strip()
    if name and "@" not in name:
        return name
    local, _, domain = email.partition("@")
    pretty = " ".join(p.capitalize() for p in re.split(r"[._-]+", local) if p)
    return f"{pretty} ({domain})" if pretty and domain else email


def email_for(name: str, users: list[tuple[str, str]]) -> str:
    """The one org account whose display name matches, or ''."""
    hits = {email for uname, email in users if _same_person(name, uname)}
    return hits.pop() if len(hits) == 1 else ""


# ── Transcript metrics ───────────────────────────────────────────────────────

def _clock(seconds: float) -> str:
    whole = int(seconds)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def speaker_stats(lines: list[dict]) -> dict[str, dict]:
    """Per transcript speaker: speaking seconds, words, questions, longest uninterrupted turn."""
    stats: dict[str, dict] = {}
    turns: list[tuple[str, float]] = []  # (speaker, start) of each change of speaker
    for i, line in enumerate(lines):
        speaker = line.get("speaker") or "Unknown"
        text = line.get("text") or ""
        words = len(text.split())
        start = float(line.get("start") or 0)
        nxt = float(lines[i + 1].get("start") or start) if i + 1 < len(lines) else None
        seconds = min(max(nxt - start, 0), _MAX_LINE_SECONDS) if nxt is not None \
            else min(words / _WORDS_PER_SECOND, _MAX_LINE_SECONDS)
        s = stats.setdefault(speaker, {"seconds": 0.0, "words": 0, "questions": 0, "longest": 0.0})
        s["seconds"] += seconds
        s["words"] += words
        s["questions"] += text.count("?")
        if not turns or turns[-1][0] != speaker:
            turns.append((speaker, start))
    end = float(lines[-1].get("start") or 0) + _MAX_LINE_SECONDS if lines else 0
    for i, (speaker, start) in enumerate(turns):
        until = turns[i + 1][1] if i + 1 < len(turns) else end
        stats[speaker]["longest"] = max(stats[speaker]["longest"], until - start)
    return stats


def attach_metrics(result: dict, lines: list[dict], client_names: list[str] = ()) -> dict:
    """
    Add free metrics to an AI analysis: per employee talk share / questions / longest turn,
    the CloudFuze-vs-client talk split, and each employee's account email. Anyone matching a
    known client attendee is dropped from "employees" (the model can mislabel them).
    """
    users = org_users()
    result["employees"] = [
        e for e in result.get("employees", [])
        if email_for(e["name"], users) or not any(_same_person(e["name"], c) for c in client_names)
    ]
    stats = speaker_stats(lines) if lines else {}
    total = sum(s["seconds"] for s in stats.values()) or 0
    employee_speakers: set[str] = set()
    for emp in result.get("employees", []):
        emp["email"] = email_for(emp["name"], users)
        speakers = [sp for sp in stats if _same_person(sp, emp["name"])]
        employee_speakers.update(speakers)
        if not speakers or not total:
            emp["metrics"] = None
            continue
        secs = sum(stats[sp]["seconds"] for sp in speakers)
        emp["metrics"] = {
            "talk_share": round(100 * secs / total),
            "questions": sum(stats[sp]["questions"] for sp in speakers),
            "longest_turn": _clock(max(stats[sp]["longest"] for sp in speakers)),
        }
    if total and employee_speakers:
        ours = sum(stats[sp]["seconds"] for sp in employee_speakers)
        result["talk_split"] = {"cloudfuze": round(100 * ours / total),
                                "client": 100 - round(100 * ours / total)}
    else:
        result["talk_split"] = None
    return result


# ── One analysis per meeting ─────────────────────────────────────────────────

def shared_analysis(rec: MeetingRecord) -> MeetingAnalysis | None:
    """
    The analysis of this meeting occurrence, whoever ran it: every attendee has their own
    MeetingRecord for the same meeting, and they should all see (and pay for) one analysis.
    """
    if rec is None:
        return None
    own = db.session.get(MeetingAnalysis, rec.id)
    if own is not None or not rec.join_url:
        return own
    return (
        MeetingAnalysis.query.join(MeetingRecord)
        .filter(MeetingRecord.join_url == rec.join_url, MeetingRecord.start_utc == rec.start_utc)
        .order_by(MeetingAnalysis.created_at.desc())
        .first()
    )


# ── Employee progress (manager view) ─────────────────────────────────────────

def employee_progress(email: str, name: str, limit: int = 12) -> dict | None:
    """
    Roll-up of every analyzed meeting this employee spoke in (matched by account email,
    else display name): averages per skill, client mood, recent ratings and themes.
    None when nothing has been analyzed for them yet.
    """
    from app.mom_generator import SKILLS

    email = (email or "").lower()
    rows = (
        db.session.query(MeetingAnalysis, MeetingRecord)
        .join(MeetingRecord, MeetingAnalysis.meeting_record_id == MeetingRecord.id)
        .order_by(MeetingRecord.start_utc.desc())
        .all()
    )
    seen: set[tuple[str, datetime]] = set()
    meetings = []
    for analysis, rec in rows:
        key = (rec.join_url or str(rec.id), rec.start_utc)
        if key in seen:
            continue
        try:
            data = json.loads(analysis.result_json)
        except ValueError:
            continue
        me = next((e for e in data.get("employees", [])
                   if (email and e.get("email") == email) or (name and _same_person(e.get("name", ""), name))),
                  None)
        if me is None:
            continue
        seen.add(key)
        meetings.append({
            "record": rec, "employee": me,
            "mood": (data.get("client_mood") or data.get("client_satisfaction") or {}).get("level", "unclear"),
            "escalation": (data.get("escalation") or {}),
        })
    if not meetings:
        return None

    def avg(values):
        values = [v for v in values if isinstance(v, (int, float))]
        return round(sum(values) / len(values), 1) if values else None

    skills = {k: avg([(m["employee"].get("scores") or {}).get(k) for m in meetings]) for k in SKILLS}
    scored = {k: v for k, v in skills.items() if v is not None}
    moods = {level: sum(1 for m in meetings if m["mood"] == level)
             for level in ("satisfied", "neutral", "dissatisfied", "unclear")}
    escalations = [m for m in meetings if m["escalation"].get("is_escalation")]

    def own_event(rec: MeetingRecord) -> str | None:
        """This employee's own copy of the meeting (links stay inside what they may view)."""
        if rec.owner_email == email:
            return rec.event_id
        mine = MeetingRecord.query.filter_by(owner_email=email, join_url=rec.join_url,
                                             start_utc=rec.start_utc).first()
        return mine.event_id if mine else None

    return {
        "count": len(meetings),
        "avg_rating": avg([m["employee"].get("rating") for m in meetings]),
        "skills": skills,
        "best_skill": max(scored, key=scored.get) if scored else None,
        "weakest_skill": min(scored, key=scored.get) if scored else None,
        "moods": moods,
        "escalations": len(escalations),
        "escalations_resolved": sum(1 for m in escalations if m["escalation"].get("resolved") == "yes"),
        # Oldest -> newest, for the trend bars
        "recent": [{"subject": m["record"].subject, "start": m["record"].start_utc,
                    "event_id": own_event(m["record"]), "rating": m["employee"].get("rating"),
                    "mood": m["mood"]}
                   for m in reversed(meetings[:limit])],
        "improvements": [{"subject": m["record"].subject, "point": p.get("point", "")}
                         for m in meetings for p in m["employee"].get("improvements", [])][:6],
        "strengths": [{"subject": m["record"].subject, "point": p.get("point", "")}
                      for m in meetings for p in m["employee"].get("strengths", [])][:4],
    }
