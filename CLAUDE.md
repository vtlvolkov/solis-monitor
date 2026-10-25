# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Docker Compose stack that monitors a Solis S6-EH1P hybrid inverter over Modbus TCP: `poller` (Python) → `influxdb` (2.7) → `web` (FastAPI + Preact dashboard). Deployed on an Umbrel box; README.md is the operator guide. The dashboard's Settings page can change three battery settings through the poller (off unless `CONTROL_ENABLED=true` and `WEB_BASIC_AUTH` are set).

## Commands

No linter or CI. Everything runs through Docker Compose (requires a filled-in `.env`, see `.env.example`):

```bash
docker compose up -d --build           # full stack
docker compose up -d --build poller    # after editing poller/poller.py
docker compose up -d --build web       # after editing web/
docker compose logs -f poller
```

Tests (they also run inside both Dockerfiles' test stages, so a failing test fails `docker compose build`, and in `.github/workflows/test.yml`):

```bash
cd poller && uv run --no-project --python 3.12 --with-requirements requirements-dev.txt pytest          # control + device decoding
cd web/backend && uv run --no-project --python 3.12 --with-requirements requirements-dev.txt pytest   # add -k <name> for one
cd web/frontend && npm test                                                                           # vitest; npx vitest run -t "<name>" for one
cd web/frontend && npm run build                                                                      # tsc type-check + vite build
```

## Architecture

- **`poller/poller.py`**: the only Modbus client. The inverter/logger may accept only one connection, so never add a second client (the web app must read InfluxDB only) and never run two copies of the stack. `REGISTERS` maps field → (input register, type, scale); `build_blocks()` groups them into contiguous FC04 reads; `read_snapshot()` decodes and adds derived fields. `UNIT_KW` handles pymodbus renaming the unit kwarg (`slave` → `device_id`).
- **Modbus pacing**: `Inverter.session()` serialises every request and spaces them ≥ 350 ms apart; blocks are ≤ 50 registers (Solis protocol limits). Ignoring this caused transaction-ID mismatch errors.
- **`poller/control.py`**: the write path. `Controller._plan()` validates a named setting against the inverter's current related values (e.g. SOC bounds from 43010/43018) and returns what to write; `_apply()` enforces the safety model (enabled flag, `MAX_*_CURRENT_A`, one change at a time, rate limit, 3 s read-back, audit to `solis_control`). Settings: 43110 bits 1/4/5, 43117/43118 (FC06), 43011, 43024, Time-of-Use slots 43707–43791, clock 43000–43005 (all FC16). Served by a stdlib HTTP server on `:8780` (Docker network only, `Authorization: Bearer <INFLUX_TOKEN>`). Never add raw register writes, off-grid/feed-in switching, or registers not checked against a live probe.
- **`poller/notify.py`**: Telegram alerts (`Alerts` turns tracker events, outage SOC levels and poll failures into messages; `Telegram` sends them from a background queue with retries). The poll loop keeps inverter-read errors (alerts) separate from InfluxDB write errors (logged only).
- **`poller/faults.py`**: fault/status/BMS bit tables (Solis protocol Appendix 2/5/6) and `EventTracker`, which turns each poll into debounced start/end events (grid outage, alarms) written to measurement `solis_event`.
- **`poller/device.py`**: device details for the Devices tab (`GET /device` on the same internal API; a background thread refreshes it every 60 s because a full read takes ~10 s): serial (33004–33011, ASCII), firmware, clock (33022–27), status (33095), faults (33116–20), BMS (33141–46), meter (33251–86). Mappings were verified against a live dump that is kept as the fixture in `tests/test_device.py`. Add a register only after checking it against live values the same way.
- **InfluxDB**: measurement `solis`, tag `host`, one point per poll with one float field per register plus derived fields:
  - `grid_power = -meter_power` → + import, − export
  - `battery_power_signed` → + charging, − discharging (sign from `battery_direction`: 0 = charging)
  - `total_load_power = house_load_power + backup_load_power`
- **`web/backend/app`** (FastAPI, also serves the built frontend from `static/`): `influx.py` is the only module that queries InfluxDB (tests replace it with a fake repo via `create_app(repo=...)`). `validation.py` whitelists every query parameter. InfluxDB OSS has no Flux parameter binding, so Flux strings are built only from those canonical values. `WEB_AUTH_SETTINGS_ONLY=true` limits Basic auth to `/api/settings*` (the Settings page's first fetch triggers the browser prompt; it shows a "Sign in" button on 401). `mcp_server.py` is a read-only MCP server at `/mcp` (own `MCP_TOKEN`, bypasses Basic auth; never add write tools; it reuses the route functions via `DataApi`). `control.py` is the client for the poller's internal API (`/api/settings`, `/api/devices` proxy to it); `POST /api/settings` requires `WEB_BASIC_AUTH` to be set, a JSON body, and same-origin (Origin / Sec-Fetch-Site) to block cross-site requests. `tariff.py` keeps the electricity tariff as versions with a start time (`TariffStore`: `.env` is the first version, saved changes go to `/data/tariff.json` in the `web-data` volume; `GET/POST /api/settings/tariff`, behind the same auth and same-origin checks as other changes, start at the next quarter-hour or a later date and never rewrite the past); `shaping.import_costs` prices each 15-minute window with the tariff in force at that time, so past costs never change. `GET /api/cost` returns today/month blocks (kWh and cost per zone) plus `now` (`shaping.rate_now`: the active zone, its price and when it ends), which is computed per request outside the 5-minute cache. `GET /api/cost/history?month=YYYY-MM` returns per-day grid import split into day/night zones with cost (`shaping.cost_days`; `repo.window_max` takes an optional `stop`, `repo.first_time()` gives the oldest reading) for the cost history page (`pages/CostHistory.tsx`, `charts/CostBars.tsx`, `lib/cost.ts`; URL `?view=cost&month=…&day=…`). `shaping.py` holds the pure logic: the online flag (45 s), gap insertion, and daily kWh (15-minute window maxima grouped by local day in Python).
- **`web/frontend`** (Vite + Preact + uPlot): flow-direction and dead-band rules live in `src/lib/flow.ts` (each SVG path is drawn a→b; `reversed` = b→a). Colours come only from `src/lib/tokens.ts`, which is published as `--c-*` CSS vars. Live data: `src/lib/live.ts` (SSE with polling fallback). Settings validation and change summaries: `src/lib/settings.ts`. Device status rules: `src/lib/devices.ts`. Runtime estimate and outage detection: `src/lib/runtime.ts`. Outage/alarm pairing and month/year energy sums: `shaping.pair_events` / `period_sums`. Views are URL-driven (`?view=devices&device=battery`). Translations: `src/lib/i18n.ts` (`t()` with the English text as the key, `tx()` to mark strings defined at module level, `tp()` for plurals, `unit()` for Вт/кВт·год/В/А/Гц) and `i18n.uk.ts`; every user-visible string must go through `t()` and have a Ukrainian entry (`i18n.test.ts` checks the keys found in the code, the placeholders, and the poller's fault names in `faults.py`). The four power gauges: `lib/gaugeRows.ts` builds each one's data (maximum, colour, note, loading/0 A states) and both `components/GaugeBars.tsx` draws the bars on both the Overview and the TV page. `useLang()` in `App` re-renders the tree on a language change. `?view=tv` (`pages/Tv.tsx`, `lib/tv.ts`) is the wall display for the Android TV app: a fixed 1920×1080 stage scaled to the window, no nav, read-only.
- **`android/`**: Android TV app (Kotlin, no dependencies) that shows `http://<host>:<port>/?view=tv` in a WebView; `Config.kt` parses the configurable host/port (unit-tested). Built only through Docker (`sh android/build.sh`); it is not part of `docker-compose.yml` or the deploy, and never talks to the inverter or InfluxDB directly.

## Deployment

A second site runs the same stack on a Raspberry Pi (own InfluxDB, own `.env`, reached over Tailscale; see README "Second site on a Raspberry Pi"). `deploy/pi/setup.sh` prepares the Pi, `deploy/pi/find-inverter.sh` finds the logger on its LAN. `SITE_NAME` (poller + web) labels the site. Both sites deploy from `main`.

Production runs on an Umbrel host that pulls `main` from a private GitHub repo every 3 minutes (`deploy/deploy.sh` + `deploy/solis-deploy.timer`) and runs `docker compose up -d --build`. `.env` lives only on that host. Anything pushed to `main` is deployed and can write inverter settings, so keep the test suites meaningful.

## Installer and releases (public install path)

- `install.sh` (repo root) is the one-command installer for Debian/Ubuntu/Raspberry Pi OS: prompts from `/dev/tty` (it runs piped from curl), scans the LAN for the Modbus logger, installs Docker, downloads `docker-compose.yml`, `.env.example` and `deploy/solis-monitor` from the release tag, pulls the prebuilt images, writes `/opt/solis-monitor/.env` with fresh secrets and `CONTROL_ENABLED=false`. It refuses to run over an existing install. `SOLIS_TEST_MODE=1`, `SOLIS_RAW_BASE`, `SOLIS_API_BASE`, `SOLIS_DIR` exist for the tests.
- `deploy/solis-monitor` is the management command installed next to it (`status`, `update`, `auto-update on|off`, `logs`). `update` only moves to tagged GitHub releases, restores the old compose file and version if the new one is not healthy, and never edits `.env` except `SOLIS_VERSION`.
- `docker-compose.yml` has both `image:` and `build:` for poller and web: the image defaults to `solis-monitor-<service>:latest` (what a local build is called), the installer sets `SOLIS_IMAGE_PREFIX`/`SOLIS_VERSION` in `.env` to use the registry images. The maintainer's own sites still build from `main` through `deploy/deploy.sh`.
- `.github/workflows/release.yml` builds amd64+arm64 images for a `v*` tag and creates the release with `install.sh` attached (its `DEFAULT_VERSION=` line is stamped with the tag). Keep every new compose variable optional (`${VAR:-default}`): updates replace the compose file but not `.env`.
- `deploy/tests/test-install.sh` tests both scripts with a stand-in `docker`, a fake Modbus logger and `file://` releases; it runs in CI together with shellcheck.

## Changing things

- **New register**: add it to `REGISTERS` in `poller/poller.py` (registers are 0-based input-register addresses; `u32`/`s32` span two registers, high word first) **and** to `FIELDS` in `web/backend/app/validation.py`, or the API won't accept or return it.
- `INFLUX_RETENTION` and other `DOCKER_INFLUXDB_INIT_*` values only apply on the first start of a fresh volume.
