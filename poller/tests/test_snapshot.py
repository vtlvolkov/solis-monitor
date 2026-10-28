import poller


def snapshot(regs):
    return poller.read_snapshot(lambda start, count: [regs.get(start + i, 0) for i in range(count)])


def test_battery_temperature_needs_no_extra_read():
    assert any(s <= 33043 < e for s, e in poller.BLOCKS if s <= 33029 < e)


def test_battery_temperature():
    assert snapshot({33043: 187})["battery_temperature"] == 18.7
    assert snapshot({33043: 0x10000 - 52})["battery_temperature"] == -5.2
    assert "battery_temperature" not in snapshot({33043: 0x10000 - 2001})      # not a battery temperature
    assert "battery_temperature" not in snapshot({33043: 1500})
