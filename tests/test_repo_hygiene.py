"""Repository hygiene: the committed env template holds placeholders only, and it never reaches
the Docker image."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET_KEY_NAMES = re.compile(r"(SECRET|PASSWORD|TOKEN|API_KEY|CLIENT_ID|ACCOUNT_ID|TENANT_ID)")
PLACEHOLDER = re.compile(r"your-|change|here|example|xxx|<|^$", re.IGNORECASE)


def _template_values() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if m:
            values[m.group(1)] = m.group(2).strip("'\"")
    return values


def test_env_template_has_no_real_credentials():
    real = [k for k, v in _template_values().items()
            if SECRET_KEY_NAMES.search(k) and not PLACEHOLDER.search(v)]
    assert real == [], f"non-placeholder values committed for: {real}"


def test_env_templates_are_excluded_from_the_docker_image():
    patterns = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert ".env.*" in patterns


def test_runtime_image_does_not_run_as_root():
    stages = (ROOT / "Dockerfile").read_text(encoding="utf-8").split("\nFROM ")
    runtime = stages[-1]
    users = re.findall(r"^USER\s+(\S+)", runtime, re.MULTILINE)
    assert users and users[-1] not in ("root", "0"), "runtime stage must switch to a non-root USER"
