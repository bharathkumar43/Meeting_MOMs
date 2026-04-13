import os
from dotenv import load_dotenv

load_dotenv()


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

    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    SESSION_COOKIE_HTTPONLY = True

    ORG_DOMAIN = os.getenv("ORG_DOMAIN", "cloudfuze.com")

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

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/meeting_moms",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
