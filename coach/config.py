"""App-wide settings, read from environment variables / a local .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
MODELS_DIR = BASE_DIR / "models"   # downloaded speech models (git-ignored)

DOMAIN_FILES = {
    "ml": DATA_DIR / "ml_questions.csv",
    "frontend": DATA_DIR / "frontend_questions.csv",
    "backend": DATA_DIR / "backend_questions.csv",
    "data_analyst": DATA_DIR / "data_analyst_questions.csv",
    "fullstack": DATA_DIR / "fullstack_questions.csv",
    "devops": DATA_DIR / "devops_questions.csv",
    "data_scientist": DATA_DIR / "data_scientist_questions.csv",
    "software_engineer": DATA_DIR / "software_engineer_questions.csv",
}
ROLE_LABELS = {
    "ml": "Machine Learning Engineer",
    "frontend": "Frontend Developer",
    "backend": "Backend Developer",
    "data_analyst": "Data Analyst",
    "fullstack": "Full Stack Developer",
    "devops": "DevOps / Cloud Engineer",
    "data_scientist": "Data Scientist",
    "software_engineer": "Software Engineer (General)",
}
MODES = {"technical": "Technical", "behavioral": "Behavioral (STAR)"}


# Gemini API keys belong to each user (added on the API keys page), not to the server.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
QUESTIONS_PER_INTERVIEW = int(os.getenv("QUESTIONS_PER_INTERVIEW", "5"))
MAX_FOLLOW_UPS = int(os.getenv("MAX_FOLLOW_UPS", "2"))
ENABLE_SERVER_CAMERA = os.getenv("ENABLE_SERVER_CAMERA", "1") == "1"

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://coach:coach_dev_password@127.0.0.1:5434/interview_coach"
)
SECRET_KEY = os.getenv("SECRET_KEY") or ""
if len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY must be set in .env to a random string of 32+ characters.")

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base.en")

# --- Security --------------------------------------------------------------------------------
# Key used to encrypt users' Gemini API keys at rest. Use a Fernet key in production, e.g. from
# AWS Secrets Manager:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# If unset, one is derived from SECRET_KEY (fine for local development).
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")
# Set to 1 when serving over HTTPS (e.g. behind an AWS load balancer) so cookies are Secure.
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "0") == "1"

# --- Email (password reset) ------------------------------------------------------------------
# Any SMTP server works, including AWS SES SMTP. Without SMTP_HOST, reset links are printed in
# the server log instead (development).
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "AI Interview Coach <no-reply@example.com>")
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "1") == "1"

# --- Admin panel (/admin) -----------------------------------------------------------------------
# Users marked as admin in the database (python -m coach.admin_cli make-admin EMAIL) can open
# /admin. Emails listed here (comma-separated) are treated as admins too.
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
