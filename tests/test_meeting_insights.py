"""Employee progress roll-up (manager view) and analysis sharing."""
import json
from datetime import datetime, timedelta

from app import db, meeting_insights
from app.models import MeetingAnalysis, MeetingRecord

EMP = "ana@cloudfuze.com"


def _analysis(rec, rating, name="Ana Silva", email=EMP):
    return MeetingAnalysis(meeting_record_id=rec.id, model="m", created_by="x", result_json=json.dumps({
        "client_mood": {"level": "satisfied"}, "escalation": {"is_escalation": False},
        "employees": [{"name": name, "email": email, "rating": rating,
                       "scores": {"clarity": 4}, "strengths": [{"point": "clear"}], "improvements": []}],
    }))


def _seed():
    start = datetime(2026, 9, 1, 10)
    recs = []
    for i in range(14):
        owner = EMP if i % 3 == 0 else "boss@cloudfuze.com"
        recs.append(MeetingRecord(owner_email=owner, event_id=f"o-{i}", subject=f"M{i}",
                                  start_utc=start + timedelta(days=i), join_url=f"https://teams.microsoft.com/{i}"))
    # Ana's own copies of some meetings owned by the boss
    for i in (1, 4, 13):
        recs.append(MeetingRecord(owner_email=EMP, event_id=f"mine-{i}", subject=f"M{i}",
                                  start_utc=start + timedelta(days=i), join_url=f"https://teams.microsoft.com/{i}"))
    db.session.add_all(recs)
    db.session.flush()
    db.session.add_all(_analysis(r, 5 + (k % 5)) for k, r in enumerate(recs[:14]))
    db.session.commit()


def test_employee_progress_rollup_and_links(db_session, count_queries):
    _seed()
    with count_queries() as n:
        progress = meeting_insights.employee_progress(EMP, "Ana Silva")
    assert progress["count"] == 14
    assert progress["moods"]["satisfied"] == 14
    recent = progress["recent"]  # oldest -> newest of the latest 12
    assert [m["subject"] for m in recent] == [f"M{i}" for i in range(2, 14)]
    links = {m["subject"]: m["event_id"] for m in recent}
    assert links["M3"] == "o-3"          # Ana owns it
    assert links["M4"] == "mine-4"       # boss owns it, Ana has her own copy
    assert links["M13"] == "mine-13"
    assert links["M5"] is None           # no copy of Ana's: no link
    assert n["count"] <= 3, n["count"]


def test_shared_analysis_is_found_from_another_attendees_record(db_session):
    _seed()
    mine = MeetingRecord.query.filter_by(event_id="mine-4").one()
    shared = meeting_insights.shared_analysis(mine)
    assert shared is not None
    assert db.session.get(MeetingRecord, shared.meeting_record_id).event_id == "o-4"
