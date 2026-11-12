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


def test_restored_outage_is_not_announced_again_but_ends_with_its_real_duration():
    sent, clock = [], Clock()
    a = Alerts(sent.append, soc_levels=(50, 30, 20), offline_after_s=300, battery=lambda: (5.12, 20), clock=clock)
    a.restore([{"key": "grid", "kind": "outage", "name": "Grid outage", "severity": "warning",
                "since": clock() - 3600, "soc": 88.0},
               {"key": "f2b1", "kind": "alarm", "name": "Backup overload", "severity": "fault", "since": clock() - 600}])
    assert sent == []                                                                  # picking up quietly
    a.snapshot({"battery_soc": 45.0, "total_load_power": 500.0})
    assert len(sent) == 1 and "45%" in sent[0]                                         # the levels crossed meanwhile, once
    clock.t += 600
    a.events([{"kind": "alarm", "key": "f2b1", "name": "Backup overload", "severity": "fault", "state": "end",
               "soc": 44.0, "load": 500.0},
              {"kind": "outage", "key": "grid", "name": "Grid outage", "severity": "warning", "state": "end",
               "soc": 44.0, "load": 500.0}], {})
    assert "after 20 min" in sent[1] and "Outage lasted 1 h 10 min" in sent[2] and "88% → 44%" in sent[2]


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


def test_clock_drift_needs_three_reads_then_one_message_each_way():
    a, sent, _ = make()
    a.clock_drift(-720.0, "r1"); a.clock_drift(-720.0, "r1"); a.clock_drift(-720.0, "r1")     # the same read, polled thrice
    assert sent == []
    a.clock_drift(-725.0, "r2")
    assert sent == []                                                                        # two reads: a glitch could do that
    a.clock_drift(-730.0, "r3")
    assert len(sent) == 1 and "12 min behind" in sent[0] and "shows 18:51 at 19:04" in sent[0] and "Settings → General" in sent[0]
    a.clock_drift(-735.0, "r4")
    assert len(sent) == 1                                                                    # not again while it stays off
    a.clock_drift(3.0, "r5"); a.clock_drift(2.0, "r6")
    assert len(sent) == 1
    a.clock_drift(1.0, "r7")
    assert len(sent) == 2 and "right again" in sent[1]
    a.clock_drift(400.0, "r8"); a.clock_drift(400.0, "r9"); a.clock_drift(400.0, "r10")
    assert len(sent) == 3 and "7 min ahead" in sent[2]


def test_clock_drift_can_be_turned_off_and_ignores_an_unreadable_clock():
    a, sent, _ = make(clock_drift_s=0)
    for i in range(4):
        a.clock_drift(-3600.0, f"r{i}")
    assert sent == []
    a, sent, _ = make()
    for i in range(4):
        a.clock_drift(None, f"r{i}")
    a.clock_drift(10.0, "r9"); a.clock_drift(10.0, "r10"); a.clock_drift(10.0, "r11")      # within 5 min: fine
    assert sent == []


def test_clock_drift_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("ALERT_CLOCK_DRIFT_MIN", "2")
    assert notify.from_env().clock_drift_s == 120.0


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
