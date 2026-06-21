"""Self-hosted Capgo update manifest. Pure logic, no FastAPI, so it's unit-testable."""
import json
from pathlib import Path


def build_manifest(latest: dict | None, current_version: str | None, public_base: str) -> dict:
    """Capacitor-updater self-hosted response: an update payload when a newer
    bundle exists, else an explicit up-to-date no-op (no 'version' key, so the
    plugin does nothing and fires its noNeedUpdate event). Shape confirmed
    against the plugin's Android source in Task 1."""
    no_update = {
        "error": "no_new_version_available",
        "kind": "up_to_date",
        "message": "no update available",
    }
    if not latest or not latest.get("version") or not latest.get("file"):
        return no_update
    if current_version == latest["version"]:
        return no_update
    return {
        "version": latest["version"],
        "url": f"{public_base}/app/bundles/{latest['file']}",
        "checksum": latest.get("checksum", ""),
    }


def read_latest(bundles_dir: Path) -> dict | None:
    p = bundles_dir / "latest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None
