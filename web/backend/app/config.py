"""Settings from the environment (same INFLUX_* variables as the poller)."""
import os
from dataclasses import dataclass, field


def _float(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _retention(v) -> int:
    """The same reading of RAW_RETENTION_DAYS as the poller's (downsample.parse_retention): 90 by default, 0 = all, at least 35."""
    try:
        days = int(str(v).strip()) if v not in (None, "") else 90
    except ValueError:
        return 90
    return 0 if days <= 0 else max(days, 35)


@dataclass(frozen=True)
class Settings:
    influx_url: str = field(default_factory=lambda: os.getenv("INFLUX_URL", "http://influxdb:8086"))
    influx_token: str = field(default_factory=lambda: os.getenv("INFLUX_TOKEN", ""))
    influx_org: str = field(default_factory=lambda: os.getenv("INFLUX_ORG", "home"))
    influx_bucket: str = field(default_factory=lambda: os.getenv("INFLUX_BUCKET", "solis"))
    # 1-minute aggregates (mean/min/max per field), kept by an InfluxDB task the poller sets up; read for a day or longer
    influx_bucket_1m: str = field(default_factory=lambda: os.getenv("INFLUX_BUCKET_1M") or f'{os.getenv("INFLUX_BUCKET", "solis")}_1m')
    # Days of 10-second readings the poller keeps (0 = all; see poller/downsample.py). Grid quality and the 10 s export
    # need them, so they refuse older periods instead of showing nothing.
    raw_retention_days: int = field(default_factory=lambda: _retention(os.getenv("RAW_RETENTION_DAYS")))
    tz: str = field(default_factory=lambda: os.getenv("TZ", "Europe/Kyiv") or "Europe/Kyiv")
    basic_auth: str = field(default_factory=lambda: os.getenv("WEB_BASIC_AUTH", ""))
    auth_file: str = field(default_factory=lambda: os.getenv("AUTH_FILE", "/data/auth.json"))     # a password changed on the Settings page ("" = cannot be changed)
    # With WEB_BASIC_AUTH set: true = only the Settings API asks for the password, the rest stays open.
    auth_settings_only: bool = field(default_factory=lambda: os.getenv("WEB_AUTH_SETTINGS_ONLY", "").strip().lower()
                                     in ("1", "true", "yes", "on"))
    site_name: str = field(default_factory=lambda: os.getenv("SITE_NAME", "").strip())
    tariff_file: str = field(default_factory=lambda: os.getenv("TARIFF_FILE", "/data/tariff.json"))   # saved tariff changes ("" = memory only)
    layout_file: str = field(default_factory=lambda: os.getenv("LAYOUT_FILE", "/data/overview-layout.json"))   # the Overview's cards ("" = memory only)
    # Release installs (install.sh) know their version and repository; builds from source do not.
    solis_version: str = field(default_factory=lambda: os.getenv("SOLIS_VERSION", "").strip())
    solis_repo: str = field(default_factory=lambda: os.getenv("SOLIS_REPO", "").strip())
    # Set by a platform that updates the app itself (the Umbrel app): the dashboard then shows the version but offers no update.
    updates_managed_by: str = field(default_factory=lambda: os.getenv("UPDATES_MANAGED_BY", "").strip())
    update_dir: str = field(default_factory=lambda: os.getenv("UPDATE_DIR", "/control"))     # shared with the host, see updates.py
    backup_dir: str = field(default_factory=lambda: os.getenv("BACKUP_DIR", "/backups"))     # the host's backups/, read-only here (backups.py)
    github_api: str = field(default_factory=lambda: os.getenv("GITHUB_API", "https://api.github.com"))
    # Look for a newer release in the background every N hours (0 = never; only for release installs).
    update_check_hours: float = field(default_factory=lambda: _float(os.getenv("UPDATE_CHECK_HOURS", "6")) or 0.0)
    update_check_first_delay_s: float = 120.0
    mcp_token: str = field(default_factory=lambda: os.getenv("MCP_TOKEN", ""))
    poller_url: str = field(default_factory=lambda: os.getenv("POLLER_URL", "http://poller:8780"))
    # The secret for the poller's internal API. Its own variable (CONTROL_TOKEN), so this container can run with a
    # read-only InfluxDB token; installs from before it existed share INFLUX_TOKEN with the poller instead.
    control_token: str = field(default_factory=lambda: os.getenv("CONTROL_TOKEN") or os.getenv("INFLUX_TOKEN", ""))
    # Electricity tariff for the cost card. No day price = not configured.
    tariff_currency: str = field(default_factory=lambda: os.getenv("TARIFF_CURRENCY", "UAH") or "UAH")
    tariff_day: float | None = field(default_factory=lambda: _float(os.getenv("TARIFF_DAY")))
    tariff_night: float | None = field(default_factory=lambda: _float(os.getenv("TARIFF_NIGHT")))
    tariff_night_start: str = field(default_factory=lambda: os.getenv("TARIFF_NIGHT_START", "23:00") or "23:00")
    tariff_night_end: str = field(default_factory=lambda: os.getenv("TARIFF_NIGHT_END", "07:00") or "07:00")
    stream_interval_s: float = field(default_factory=lambda: float(os.getenv("STREAM_INTERVAL_S", "10")))
    static_dir: str = field(default_factory=lambda: os.getenv(
        "STATIC_DIR", os.path.join(os.path.dirname(__file__), "..", "static")))
