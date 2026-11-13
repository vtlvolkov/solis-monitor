"""Settings read from the environment."""
from app.config import Settings


def test_control_token_has_its_own_variable_and_falls_back_to_the_database_token(monkeypatch):
    monkeypatch.setenv("INFLUX_TOKEN", "admin")
    monkeypatch.delenv("CONTROL_TOKEN", raising=False)
    assert Settings().control_token == "admin"            # an install from before CONTROL_TOKEN existed
    monkeypatch.setenv("CONTROL_TOKEN", "")
    assert Settings().control_token == "admin"
    monkeypatch.setenv("CONTROL_TOKEN", "ctl")
    s = Settings()
    assert s.control_token == "ctl" and s.influx_token == "admin"


def test_raw_retention_reads_like_the_pollers(monkeypatch):
    for raw, want in ((None, 90), ("", 90), ("0", 0), ("-1", 0), ("120", 120), ("7", 35), ("x", 90)):
        if raw is None:
            monkeypatch.delenv("RAW_RETENTION_DAYS", raising=False)
        else:
            monkeypatch.setenv("RAW_RETENTION_DAYS", raw)
        assert Settings().raw_retention_days == want, raw
