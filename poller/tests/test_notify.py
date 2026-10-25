import threading
import time

import notify
from notify import Alerts, Telegram

KYIV_1904 = 1792339440.0       # 2026-10-18 19:04 Kyiv


class Clock:
    def __init__(self, t=KYIV_1904):
        self.t = t

    def __call__(self):
        return self.t


def make(**kw):
    sent, clock = [], Clock()
    a = Alerts(sent.append, soc_levels=(50, 30, 20), offline_after_s=300, battery=lambda: (5.12, 20),
               tz="Europe/Kyiv", clock=clock, **kw)
    return a, sent, clock


def ev(kind, state, soc=None, load=650.0, key="grid", name="Grid outage", severity="warning"):
    return {"kind": kind, "key": key, "name": name, "severity": severity, "state": state, "soc": soc, "load": load}


def test_site_name_prefixes_every_message():
    posted = []
    tg = Telegram("t", "c", post=posted.append, backoff_s=0, site=" Cottage ")
    tg.send("hello")
    for _ in range(100):
        if posted:
            break
        time.sleep(0.01)
    assert posted == ["[Cottage] hello"]


def test_site_name_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("SITE_NAME", "Home")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    assert notify.telegram_from_env().site == "Home"          # also what `notify.py --test` uses
    assert notify.from_env().telegram.site == "Home"


def test_outage_start_and_end():
    a, sent, clock = make()
    a.events([ev("outage", "start", soc=98)], {})
    assert sent[-1] == "⚡ Power outage at 19:04. Running on battery: 98%, ≈ 6 h 10 min at 0.65 kW."
    clock.t += 2 * 3600 + 11 * 60
    a.events([ev("outage", "end", soc=71)], {})
    assert sent[-1] == "✅ Power is back (21:15). Outage lasted 2 h 11 min; battery 98% → 71%."


def test_soc_levels_once_each_during_outage_only():
    a, sent, _ = make()
    a.snapshot({"battery_soc": 40})                         # no outage: nothing
    assert sent == []
    a.events([ev("outage", "start", soc=60)], {})
    for soc in (55, 50, 49, 45, 30, 29, 10):
        a.snapshot({"battery_soc": soc, "total_load_power": 600})
    msgs = [m for m in sent if m.startswith("🔋")]
    assert len(msgs) == 3                                   # 50, 30, then 20 (crossed at 10)
    assert msgs[0].startswith("🔋 Battery at 50% during the outage: ≈ 2 h 30 min left")
    assert msgs[2] == "🔋 Battery at 10% during the outage: < 10 min left at 0.60 kW."   # below the 20% floor


def test_outage_starting_below_a_level_skips_it():
    a, sent, _ = make()
    a.events([ev("outage", "start", soc=40)], {})
    a.snapshot({"battery_soc": 39, "total_load_power": 600})
    assert not any(m.startswith("🔋") for m in sent)       # 50 already below at start
    a.snapshot({"battery_soc": 30, "total_load_power": 600})
    assert sent[-1].startswith("🔋 Battery at 30%")


def test_alarm_messages():
    a, sent, clock = make()
    a.events([ev("alarm", "start", key="f2b1", name="Backup overload", severity="fault")], {})
    clock.t += 90
    a.events([ev("alarm", "end", key="f2b1", name="Backup overload", severity="fault")], {})
    assert sent == ["⚠️ Inverter alarm: Backup overload (fault).", "Alarm cleared: Backup overload after 2 min."]


def test_offline_after_threshold_and_recovery():
    a, sent, clock = make()
    a.poll_failed(TimeoutError("timed out"))
    clock.t += 200
    a.poll_failed(TimeoutError("timed out"))
    assert sent == []
    clock.t += 120
    a.poll_failed(TimeoutError("timed out"))
    a.poll_failed(TimeoutError("timed out"))                # only once
    assert len(sent) == 1 and sent[0].startswith("📡 Inverter not reachable for 5 min")
    clock.t += 60
    a.poll_ok()
    assert sent[-1] == "📡 Inverter reachable again after 6 min."
    a.poll_ok()
    assert len(sent) == 2


def test_runtime_unknown_without_battery_info():
    sent = []
    a = Alerts(sent.append, battery=lambda: None, clock=Clock())
    a.events([ev("outage", "start", soc=98)], {})
    assert "unknown" in sent[0]


def test_telegram_disabled_without_config():
    calls = []
    t = Telegram("", "123", post=calls.append)
    t.send("x")
    assert not t.enabled and calls == []


def test_telegram_retries_in_background():
    calls, done = [], threading.Event()

    def post(text):
        calls.append(text)
        if len(calls) < 2:
            raise OSError("network down")
        done.set()

    t = Telegram("tok", "123", post=post, backoff_s=0.01)
    t.send("hello")
    assert done.wait(2) and calls == ["hello", "hello"]


def test_formatters():
    assert notify.fmt_duration(90) == "2 min"
    assert notify.fmt_duration(3600 * 2 + 600) == "2 h 10 min"
    assert notify.fmt_runtime(None) == "unknown"
    assert notify.fmt_runtime(0.1) == "< 10 min"
    assert notify.fmt_runtime(7.74) == "≈ 7 h 40 min"
