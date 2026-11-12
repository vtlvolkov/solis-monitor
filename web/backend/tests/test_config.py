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
