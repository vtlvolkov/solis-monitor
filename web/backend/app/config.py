"""Settings from the environment (same INFLUX_* variables as the poller)."""
import os
from dataclasses import dataclass, field


def _float(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Settings:
    influx_url: str = field(default_factory=lambda: os.getenv("INFLUX_URL", "http://influxdb:8086"))
    influx_token: str = field(default_factory=lambda: os.getenv("INFLUX_TOKEN", ""))
    influx_org: str = field(default_factory=lambda: os.getenv("INFLUX_ORG", "home"))
    influx_bucket: str = field(default_factory=lambda: os.getenv("INFLUX_BUCKET", "solis"))
    tz: str = field(default_factory=lambda: os.getenv("TZ", "Europe/Kyiv") or "Europe/Kyiv")
    basic_auth: str = field(default_factory=lambda: os.getenv("WEB_BASIC_AUTH", ""))
    # With WEB_BASIC_AUTH set: true = only the Settings API asks for the password, the rest stays open.
    auth_settings_only: bool = field(default_factory=lambda: os.getenv("WEB_AUTH_SETTINGS_ONLY", "").strip().lower()
                                     in ("1", "true", "yes", "on"))
    site_name: str = field(default_factory=lambda: os.getenv("SITE_NAME", "").strip())
    tariff_file: str = field(default_factory=lambda: os.getenv("TARIFF_FILE", "/data/tariff.json"))   # saved tariff changes ("" = memory only)
    # Release installs (install.sh) know their version and repository; builds from source do not.
    solis_version: str = field(default_factory=lambda: os.getenv("SOLIS_VERSION", "").strip())
    solis_repo: str = field(default_factory=lambda: os.getenv("SOLIS_REPO", "").strip())
    update_dir: str = field(default_factory=lambda: os.getenv("UPDATE_DIR", "/control"))     # shared with the host, see updates.py
    github_api: str = field(default_factory=lambda: os.getenv("GITHUB_API", "https://api.github.com"))
    mcp_token: str = field(default_factory=lambda: os.getenv("MCP_TOKEN", ""))
    poller_url: str = field(default_factory=lambda: os.getenv("POLLER_URL", "http://poller:8780"))
    # Electricity tariff for the cost card. No day price = not configured.
    tariff_currency: str = field(default_factory=lambda: os.getenv("TARIFF_CURRENCY", "UAH") or "UAH")
    tariff_day: float | None = field(default_factory=lambda: _float(os.getenv("TARIFF_DAY")))
    tariff_night: float | None = field(default_factory=lambda: _float(os.getenv("TARIFF_NIGHT")))
    tariff_night_start: str = field(default_factory=lambda: os.getenv("TARIFF_NIGHT_START", "23:00") or "23:00")
    tariff_night_end: str = field(default_factory=lambda: os.getenv("TARIFF_NIGHT_END", "07:00") or "07:00")
    stream_interval_s: float = field(default_factory=lambda: float(os.getenv("STREAM_INTERVAL_S", "10")))
    static_dir: str = field(default_factory=lambda: os.getenv(
        "STATIC_DIR", os.path.join(os.path.dirname(__file__), "..", "static")))
