# MQTT and Home Assistant

The poller can publish the readings, and the outages and alarms as they happen, to your MQTT broker. Home Assistant
picks the inverter up as a device with its sensors, with nothing to set up there; Node-RED, a Shelly or Tasmota relay,
evcc or a small ESP32 display can use the same messages.

It only publishes. The poller never subscribes to anything, so no MQTT message can reach the inverter; changing inverter
settings stays on the dashboard, with its own safety rules ([Changing inverter settings](settings.md)). Turning MQTT on
adds no Modbus traffic: the readings are the ones the poller already takes. It is also the way to have the inverter in
Home Assistant at all: the logger accepts one connection, so Home Assistant must not read it over Modbus at the same time.

## Turning it on

**Settings → System → MQTT · Home Assistant**: the broker's address and port (1883, or 8883 with **Encrypted (TLS)**),
the username and password if your broker asks for them, the topic prefix (default `solis/<site name>`) and how often
to send the readings (10, 30 or 60 seconds). **Test connection** tries the settings without saving them; **Turn on**
saves them and starts. The card shows whether the poller is connected, when it last sent and how many messages, and
**Show the last message** shows exactly what was sent.

Changing these needs the dashboard login (`WEB_BASIC_AUTH`), but not `CONTROL_ENABLED`: nothing goes to the inverter.
The settings are kept in `/data/mqtt.json` in the poller's volume, readable by the poller only; the password stays
there, like it would in `.env`, and is never sent back to the browser. The `MQTT_*` values in `.env` are the fallback
for an install set up by file; a value saved on the card wins over them.

You need a broker: Mosquitto, for example the **Mosquitto broker** add-on of Home Assistant OS, or one on the same
machine. Give the poller its own MQTT user. Use the broker's address on your home network, not `localhost` or
`127.0.0.1`: the poller runs in its own container, where those mean the container itself.

## Topics

| Topic | When | What |
|---|---|---|
| `<prefix>/state` | every 10, 30 or 60 s, and at once on an outage or alarm | one JSON message with every reading below (retained, so a new subscriber gets it at once) |
| `<prefix>/event` | when an outage or alarm starts or ends | `{"time", "kind": "outage"\|"alarm", "key", "name", "severity", "state": "start"\|"end", "soc", "load"}` (QoS 1, not retained) |
| `<prefix>/status` | on connect and disconnect | `online` / `offline` (retained; the broker sets `offline` itself if the poller disappears) |
| `homeassistant/…/config` | once per connection | Home Assistant discovery, if it is on |

`<prefix>/state`, as it looks:

```json
{"time": "2026-11-13T16:17:00+00:00", "online": true, "grid_connected": true, "alarms": [],
 "battery_soc": 74, "battery_power": 768, "pv_power": 2410, "grid_power": 0,
 "house_load_power": 980, "backup_load_power": 290, "total_load_power": 1270,
 "grid_voltage": 231.0, "grid_frequency": 50.01, "backup_voltage": 229.6,
 "battery_voltage": 52.3, "battery_current": 21.6, "battery_soh": 100, "battery_temperature": 21.4,
 "inverter_temperature": 48.2, "backup_runtime_h": 6.3,
 "pv_energy_today": 14.8, "grid_import_today": 4.6, "grid_export_today": 0.0,
 "battery_charge_today": 8.4, "battery_discharge_today": 1.2, "load_today": 12.9,
 "pv_energy_total": 6120, "grid_import_total": 2210, "grid_export_total": 15,
 "next_outage": "2026-11-13T21:00:00+02:00"}
```

Power in W (`battery_power`: + charging, − discharging; `grid_power`: + import, − export), energy in kWh.
`backup_runtime_h` is the time the battery would last at the current load, down to the backup floor;
`next_outage` is the start of the next planned outage when an [outage schedule](dashboard.md#planned-outages-графік-відключень)
is set, otherwise `null`. A reading the inverter did not give is `null`.

## Home Assistant

With **Home Assistant discovery** on (the default) and Home Assistant's MQTT integration connected to the same broker,
**Settings → Devices** gets a device **Solis · <site>** with the inverter's model code, firmware and serial number and
about 25 sensors: battery, PV, grid and load power, voltages, frequency, temperatures, backup runtime, today's energy,
the next planned outage, and two binary sensors, **Grid** (on while the grid is there) and **Alarm**. The lifetime
counters are there too, hidden until you enable them. When the poller stops, the entities show as unavailable.
Turning discovery off removes them from Home Assistant.

**Energy dashboard** (Settings → Dashboards → Energy): *Grid consumption* = Grid import today, *Return to grid* = Grid
export today, *Solar production* = PV energy today, *Battery* = Battery charged today / discharged today. Home
Assistant counts the daily reset of these counters itself.

Two automations, as examples (your relay's name will differ):

```yaml
alias: Water heater on surplus
triggers:
  - trigger: numeric_state
    entity_id: sensor.solis_home_battery_state_of_charge
    above: 95
conditions:
  - condition: numeric_state
    entity_id: sensor.solis_home_pv_power
    above: 2000
actions:
  - action: switch.turn_on
    target: { entity_id: switch.water_heater }
```

```yaml
alias: Outage, shed loads
triggers:
  - trigger: state
    entity_id: binary_sensor.solis_home_grid
    to: "off"
actions:
  - action: switch.turn_off
    target: { entity_id: [switch.water_heater, switch.ev_charger] }
```

Two sites (two installs with different site names) publish under different prefixes and appear as two devices.

## Settings in .env

| Env var | Default | |
|---|---|---|
| `MQTT_HOST` | empty | The broker; empty = off (unless set on the Settings card) |
| `MQTT_PORT` | `1883` | `8883` with TLS |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | empty | |
| `MQTT_TLS` | `false` | Encrypted connection, checked against the system's certificate authorities |
| `MQTT_PREFIX` | `solis/<SITE_NAME>` | Topics start with it |
| `MQTT_INTERVAL` | `10` | Seconds between readings: 10, 30 or 60 |
| `MQTT_DISCOVERY` | `true` | Home Assistant discovery |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | Home Assistant's discovery prefix, if you changed it there |

A broker that is down never slows the readings: the poller tries again in the background (1 s, then up to a minute
apart), the latest readings replace the previous ones, and up to 50 outage and alarm messages wait for the broker.
