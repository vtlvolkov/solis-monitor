import poller


def snapshot(regs):
    return poller.read_snapshot(lambda start, count: [regs.get(start + i, 0) for i in range(count)])


def test_battery_temperature_needs_no_extra_read():
    assert any(s <= 33044 < e for s, e in poller.BLOCKS if s <= 33029 < e)


def test_battery_temperature():
    assert snapshot({33044: 2679})["battery_temperature"] == 26.79
    assert snapshot({33044: 0x10000 - 520})["battery_temperature"] == -5.2
    assert "battery_temperature" not in snapshot({33044: 0x10000 - 4001})      # not a battery temperature
    assert "battery_temperature" not in snapshot({33044: 9001})
