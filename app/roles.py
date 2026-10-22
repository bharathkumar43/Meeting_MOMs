"""
Roles and access: admin / manager / employee.

* Admins (Config.ADMIN_EMAILS — permanent — or role 'admin' set by an admin) see and
  manage everyone.
* Managers (role 'manager' set by an admin; Config.MANAGER_EMAILS is only the default until
  an admin sets a role) see only employees assigned to them.
* Employees see only their own meetings.

Every server-side check for viewing another user's meetings goes through can_view().
"""
import logging
from datetime import datetime

from flask import g

from app import db
from app.models import UserRole
from config import Config

logger = logging.getLogger(__name__)

ROLES = ("employee", "manager", "admin")
ROLE_LABELS = {"admin": "Admin", "manager": "Manager", "employee": "Employee"}


def _norm(email: str | None) -> str:
    return (email or "").strip().lower()


def _env_admins() -> set:
    return {_norm(e) for e in Config.ADMIN_EMAILS}


def _env_managers() -> set:
    return {_norm(e) for e in (Config.MANAGER_EMAILS or [])}


def _row(email: str) -> UserRole | None:
    """UserRole for an email, memoized for the current request."""
    email = _norm(email)
    cache = g.setdefault("_role_rows", {}) if _has_g() else {}
    if email not in cache:
        cache[email] = db.session.get(UserRole, email) if email else None
    return cache[email]


def prefetch(emails: list[str]) -> None:
    """Load the UserRole rows of many emails with one query into the per-request memo."""
    if not _has_g():
        return
    cache = g.setdefault("_role_rows", {})
    wanted = {_norm(e) for e in emails if _norm(e)} - set(cache)
    if not wanted:
        return
    found = {r.email: r for r in UserRole.query.filter(UserRole.email.in_(wanted)).all()}
    for email in wanted:
        cache[email] = found.get(email)


def _has_g() -> bool:
    try:
        g._probe = True
        return True
    except RuntimeError:
        return False


def is_env_admin(email: str) -> bool:
    """Admins fixed in .env — always admin, can't be changed from the UI."""
    return _norm(email) in _env_admins()


def role_of(email: str) -> str:
    email = _norm(email)
    if not email:
        return "employee"
    if email in _env_admins():
        return "admin"
    row = _row(email)
    if row and row.role in ROLES:
        return row.role  # an admin's choice in the UI wins over MANAGER_EMAILS
    return "manager" if email in _env_managers() else "employee"


def is_admin(email: str) -> bool:
    return role_of(email) == "admin"


def is_manager(email: str) -> bool:
    return role_of(email) == "manager"


def manager_of(email: str) -> str | None:
    row = _row(email)
    return row.manager_email if row else None


def team_of(manager_email: str) -> list[str]:
    """Emails of employees assigned to this manager."""
    manager_email = _norm(manager_email)
    if not manager_email:
        return []
    return [r.email for r in UserRole.query.filter_by(manager_email=manager_email).all()]


def can_view(viewer: str, target: str) -> bool:
    """May `viewer` see `target`'s meetings? Admin: any org user; manager: their team."""
    viewer, target = _norm(viewer), _norm(target)
    if not viewer or not target:
        return False
    if viewer == target:
        return True
    if not target.endswith(f"@{Config.ORG_DOMAIN}".lower()):
        return False
    if is_admin(viewer):
        return True
    return is_manager(viewer) and manager_of(target) == viewer


def assign(email: str, role: str | None, manager_email: str | None, by: str) -> UserRole:
    """
    Admin action: set a user's role and/or manager. Raises ValueError for invalid input.
    """
    email = _norm(email)
    if not email.endswith(f"@{Config.ORG_DOMAIN}".lower()):
        raise ValueError("Only CloudFuze accounts can be assigned.")
    row = db.session.get(UserRole, email) or UserRole(email=email, role="employee")
    if role is not None:
        if role not in ROLES:
            raise ValueError("Unknown role.")
        if is_env_admin(email) and role != "admin":
            raise ValueError("This admin is set in the server configuration and can't be changed here.")
        row.role = role
        if role != "employee":
            row.manager_email = None  # managers/admins don't report to a manager here
    if manager_email is not None:
        manager_email = _norm(manager_email) or None
        if manager_email:
            if manager_email == email:
                raise ValueError("A user can't be their own manager.")
            if role_of(manager_email) != "manager":
                raise ValueError("The selected person isn't a manager.")
        row.manager_email = manager_email
    row.updated_by = _norm(by)
    row.updated_at = datetime.utcnow()
    db.session.add(row)
    # A user who stops being a manager no longer has a team
    if role is not None and role != "manager":
        UserRole.query.filter_by(manager_email=email).update({"manager_email": None})
    db.session.commit()
    if _has_g():
        g.pop("_role_rows", None)
    logger.info("Roles: %s set %s -> role=%s manager=%s", by, email, row.role, row.manager_email)
    return row
