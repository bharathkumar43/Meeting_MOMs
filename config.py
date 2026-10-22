import os
from dotenv import load_dotenv

load_dotenv()


def _secure_cookie_default(redirect_uri: str) -> bool:
    """HTTPS deployments (an https:// redirect URI) get Secure cookies unless overridden."""
    return redirect_uri.lower().startswith("https://")


class Config:
    AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
    AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
    AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")

    AUTHORITY = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}"
    REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:5100/auth/callback")

    SCOPES = [
        "User.Read",
        "Calendars.Read",
        "OnlineMeetings.Read",
        "OnlineMeetingTranscript.Read.All",
        "Mail.Send",
    ]

    GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

    APP_SCOPE = "https://graph.microsoft.com/.default"

    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")

    secure_cookie_default = staticmethod(_secure_cookie_default)

    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv(
        "SESSION_COOKIE_SECURE", str(_secure_cookie_default(REDIRECT_URI))
    ).lower() == "true"
    SESSION_COOKIE_HTTPONLY = True

    ORG_DOMAIN = os.getenv("ORG_DOMAIN", "cloudfuze.com")

    # Teams meeting sync (app/meeting_sync.py)
    # First sync stores the last N days; afterwards every new meeting is kept permanently.
    SYNC_INITIAL_DAYS = int(os.getenv("SYNC_INITIAL_DAYS", "30"))
    # false = never fetch history older than the first sync window
    SYNC_BACKFILL_ENABLED = os.getenv("SYNC_BACKFILL_ENABLED", "false").lower() == "true"
    SYNC_BACKFILL_DAYS = int(os.getenv("SYNC_BACKFILL_DAYS", "365"))
    # 0 = sync every org user who has ever signed in (no one's meetings are missed)
    SYNC_ACTIVE_USER_DAYS = int(os.getenv("SYNC_ACTIVE_USER_DAYS", "0"))
    SYNC_MEDIA_CHECKS_PER_RUN = int(os.getenv("SYNC_MEDIA_CHECKS_PER_RUN", "60"))

    # Manually uploaded meeting videos (private folder, never under static/)
    UPLOAD_DIR = os.getenv(
        "UPLOAD_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
    )
    VIDEO_UPLOAD_MAX_BYTES = int(os.getenv("VIDEO_UPLOAD_MAX_MB", "1024")) * 1024 * 1024
    # Largest request Flask accepts (video limit + headroom for the form). Only the video
    # upload route gets this much; every other route is held to REQUEST_MAX_BYTES.
    MAX_CONTENT_LENGTH = VIDEO_UPLOAD_MAX_BYTES + 10 * 1024 * 1024
    REQUEST_MAX_BYTES = int(os.getenv("REQUEST_MAX_MB", "25")) * 1024 * 1024
    MAX_FORM_MEMORY_SIZE = 500 * 1024  # per non-file form field (Flask's default, made explicit)
    # Total uploaded video per user, and free disk space always left on the upload volume
    VIDEO_USER_QUOTA_BYTES = int(os.getenv("VIDEO_USER_QUOTA_MB", "20480")) * 1024 * 1024
    UPLOAD_MIN_FREE_BYTES = int(os.getenv("UPLOAD_MIN_FREE_MB", "2048")) * 1024 * 1024
    # A .docx is a zip: refuse ones that expand past this before parsing them
    DOCX_MAX_EXPANDED_BYTES = int(os.getenv("DOCX_MAX_EXPANDED_MB", "50")) * 1024 * 1024
    # Longest transcript sent to OpenAI for a MOM (~75k tokens; a 3-hour meeting is ~150k chars)
    MOM_TRANSCRIPT_MAX_CHARS = int(os.getenv("MOM_TRANSCRIPT_MAX_CHARS", "300000"))

    # Per-user (per-IP for sign-in) request limits: name -> (requests, window in seconds).
    # Override with e.g. RATE_LIMIT_MOM_BUILDER=40/3600
    RATE_LIMITS = {
        name: tuple(int(x) for x in os.getenv(f"RATE_LIMIT_{name.upper()}", default).split("/"))
        for name, default in {
            "login": "30/600", "mom_builder": "20/3600", "analyze": "60/3600",
            "send_email": "30/3600", "upload_video": "20/3600", "upload_transcript": "60/3600",
            "parse_file": "60/3600",
        }.items()
    }

    # Time zone used for every time shown in the app and in MOM documents (default IST)
    DISPLAY_TZ_NAME = os.getenv("DISPLAY_TZ_NAME", "IST")
    DISPLAY_TZ_OFFSET_MINUTES = int(os.getenv("DISPLAY_TZ_OFFSET_MINUTES", "330"))

    MEETING_KEYWORDS = [
        kw.strip()
        for kw in os.getenv(
            "MEETING_KEYWORDS",
            "Customer Call,Migration Review,Client Meeting,Onboarding",
        ).split(",")
    ]

    ADMIN_EMAILS = [
        email.strip().lower()
        for email in os.getenv("ADMIN_EMAILS", "").split(",")
        if email.strip()
    ]

    MANAGER_EMAILS = [
        email.strip().lower()
        for email in os.getenv("MANAGER_EMAILS", "").split(",")
        if email.strip()
    ]

    # Default CC recipients added to every MOM email (pre-filled, editable by sender)
    MOM_DEFAULT_DL_EMAIL = os.getenv("MOM_DEFAULT_DL_EMAIL", "").strip()
    MOM_DEFAULT_CC_EMAIL = os.getenv("MOM_DEFAULT_CC_EMAIL", "").strip()

    ZOOM_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID", "")
    ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID", "")
    ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET", "")
    ZOOM_API_BASE = "https://api.zoom.us/v2"

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    # Cheap model for on-demand meeting conversation analysis (a 1-hour meeting is ~1 cent)
    OPENAI_ANALYSIS_MODEL = os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-4.1-mini")
    # Keep one OpenAI call (all attempts) inside gunicorn's 120 s worker timeout
    OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "100"))
    OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "0"))

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@localhost:5432/meeting_moms",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Daily audit email (Graph application Mail.Send from AUDIT_SENDER_MAILBOX)
    AUDIT_REPORT_ENABLED = os.getenv("AUDIT_REPORT_ENABLED", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    AUDIT_SENDER_MAILBOX = os.getenv("AUDIT_SENDER_MAILBOX", "").strip()
    AUDIT_REPORT_RECIPIENTS = [
        e.strip()
        for e in os.getenv("AUDIT_REPORT_RECIPIENTS", "").split(",")
        if e.strip()
    ]
    AUDIT_REPORT_DAYS = int(os.getenv("AUDIT_REPORT_DAYS", "7"))
