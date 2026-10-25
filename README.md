# Solis monitor

Monitors a Solis S6-EH1P hybrid inverter over Modbus TCP, stores the data in InfluxDB 2 and shows it on a web dashboard styled after the Solis app. The dashboard can also change three battery settings (off by default).

```
inverter :502 ◀─Modbus TCP─▶ solis-poller ──▶ InfluxDB ──▶ solis-web :8088
                             (polls every 10 s;                    │
                              internal control API :8780 ◀─────────┘ Settings page)
```

> **Run only one copy of this stack.** The inverter's Modbus port may accept only one client.
> Stop any other copy (`docker compose down`) before starting this one.

## Quick install (Debian, Ubuntu, Raspberry Pi OS)

On a 64-bit Linux machine on the same network as the inverter's data logger (a Raspberry Pi 4/5 is plenty):

```bash
curl -fsSL https://github.com/vtlvolkov/solis-monitor/releases/latest/download/install.sh | sudo bash
```

It asks for a dashboard user name and password, looks for the logger on your network (or takes its IP), installs Docker if it is missing, downloads the prebuilt images and starts everything. It takes a few minutes and ends with the dashboard address. To read the script first, download it with `curl -fsSLO <same link>`, open it, then run `sudo bash install.sh`. `--help` lists the options (`--inverter IP`, `--user`, `--password`, `--yes` for no questions, `--dry-run` to see the plan only).

- The dashboard is open on your network; the password is asked for the Settings page (`--protect-dashboard` asks for it everywhere). Do not expose the port to the internet; use a VPN such as Tailscale for remote access.
- **Changing inverter settings from the dashboard is off.** It can write to a real inverter: only the Solis S6-EH1P register map has been checked against hardware. Read "Settings" below before turning it on (`CONTROL_ENABLED=true` in `/opt/solis-monitor/.env`).
- Everything lives in `/opt/solis-monitor` (`.env` holds your settings and secrets). Data is kept in Docker volumes.
- Updates are by hand and only to tagged releases: `solis-monitor update` (rolls back if the new version does not start). `solis-monitor auto-update on` checks every night instead. Also: `solis-monitor status`, `solis-monitor logs poller`.
- Only one program may talk to the logger at a time: stop Home Assistant or any other monitor that reads it.

Maintainers: pushing a tag such as `v1.2.0` runs `.github/workflows/release.yml`, which tests and publishes the amd64 and arm64 images to `ghcr.io/<owner>/solis-monitor-{poller,web}` and creates the release with `install.sh` attached. The installer script is tested by `deploy/tests/test-install.sh` (no root or Docker needed).

## Build from source (Umbrel and other hosts)

The quick install above is the easy way. To build the images yourself, for example to change the code:

```bash
# from your Mac
scp solis-monitor.zip umbrel@umbrel.local:~
ssh umbrel@umbrel.local
unzip solis-monitor.zip && cd solis-monitor
cp .env.example .env
nano .env        # set every change-me value:  openssl rand -hex 32
docker compose up -d --build
docker compose logs -f poller      # expect "Polling <inverter-ip>:502 unit 1 every 10.0s -> ..."
```

Dashboard: `http://<umbrel-ip>:8088`. Get the IP with `hostname -I` on the Umbrel box.
If the port clashes with an Umbrel app, change `WEB_PORT` in `.env`. InfluxDB has no published port: only the poller and web service reach it, over the Docker network.

## Deploying from GitHub (maintainer setup)

This is how the author runs their own sites, building from `main` instead of using the releases; you do not need it for the quick install. The Umbrel host checks the GitHub repo every 3 minutes and redeploys when `main` has new commits (`deploy/deploy.sh`, run by a systemd timer). The Docker builds run all test suites, so a commit with failing tests stops at the build and the running version stays up. GitHub Actions (`.github/workflows/test.yml`) runs the same tests on every push. `.env` exists only on the Umbrel host and is never committed.

Pushing to `main` changes code that can write inverter settings. If you deploy like this from your own fork, keep it private (or at least protect `main`) and use 2FA on your GitHub account.

One-time setup on the Umbrel host (over SSH):

```bash
git --version                                   # git must be available on the host

# 1. Read-only deploy key: it can pull this one repo and nothing else
ssh-keygen -t ed25519 -f ~/.ssh/solis_deploy -N "" -C "umbrel solis deploy"
cat ~/.ssh/solis_deploy.pub                     # GitHub repo -> Settings -> Deploy keys -> Add (leave "write access" off)
cat >> ~/.ssh/config <<'CFG'
Host github-solis
  HostName github.com
  User git
  IdentityFile ~/.ssh/solis_deploy
  IdentitiesOnly yes
CFG

# 2. Clone and configure (stop any other copy of the stack first)
git clone git@github-solis:<you>/solis-monitor.git ~/solis-monitor
cd ~/solis-monitor
cp .env.example .env && chmod 600 .env && nano .env   # new INFLUX_TOKEN: openssl rand -hex 32
deploy/deploy.sh --force                        # first build + start; log in deploy/deploy.log

# 3. Check for new commits every 3 minutes
sudo cp deploy/solis-deploy.service deploy/solis-deploy.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now solis-deploy.timer
```

If systemd units can't be installed, use cron instead: `crontab -e`, then add `*/3 * * * * /home/umbrel/solis-monitor/deploy/deploy.sh`. Both expect the checkout at `/home/umbrel/solis-monitor`; edit the paths if yours differs.

To see what ran: `tail deploy/deploy.log` ("OK abc1234" or "FAILED …"); `deploy/last-ok` holds the last deployed commit. An agent such as Hermes can read these to report deployments without having any host access itself. Never edit files in the checkout on the server: the deploy only fast-forwards, and it stops with an error if the checkout has diverged.

## Web dashboard

- **Overview**: live power-flow diagram (PV, Grid, Grid Load, Battery, Backup Load), battery SOC bar, today's kWh, a backup-runtime estimate, and a red banner during a grid outage.
- **Devices**: inverter, battery, logger and meter cards with status, like the Solis app. Tap a card for details:
  - inverter: serial number, firmware, state, fault codes, clock drift, AC/PV/grid values
  - battery: SOC/SOH, voltage, current, today's kWh, plus BMS voltage/current, BMS charge and discharge limits, BMS failure codes
  - meter: voltage, current, active/reactive/apparent power, power factor, frequency, lifetime import/export
  - logger: address, link status, last data

  Device details are read from the inverter by the poller on request (cached 30 s). Their register meanings were checked against a live S6-EH1P.
- **Events**: grid connected or out (with time left on battery), active alarms, outage history (start, end, duration, battery SOC before → after, totals) and alarm history with decoded fault names.
- **Settings**: see [Settings](#settings) below.
- **Charts**: stats, power flows, SOC, battery voltage and current, grid and backup-output voltage, frequency, energy today, an energy summary (today / yesterday / month / year / lifetime), and daily kWh for the last 30 days. The time range is kept in the URL (`?view=charts&range=24h`).

**Backup runtime** = usable energy ÷ load, where usable energy is capacity × (SOC − the discharge floor on the backup output, 33298). The load is the 1-hour average. Capacity comes from `BATTERY_CAPACITY_KWH`; if that's unset, it's the inverter's rated-Ah setting (43019) × 51.2 V. Set it if you know your battery's usable kWh.

**Outages and alarms** are recorded by the poller into measurement `solis_event`, when the "No grid" fault bit is set, the status is 0x1015, or grid voltage drops below 100 V. A change must be seen in 2 consecutive polls. History starts when you install this version.

The Overview and the TV page show four power gauges as horizontal bars: **Grid** (the size of the grid power in either direction, with an Importing/Exporting note; scale 0–6 kW), **Backup load** (0–6 kW, the inverter's maximum), and **Battery charge** and **Battery discharge** (the battery's power in each direction, 0 while it goes the other way, with a note). The battery bars' maximum follows the **Max charge current** / **Max discharge current** setting times a voltage: the inverter's equalize voltage for charging (40 A × 55 V = 2.2 kW) and its float voltage for discharging (60 A × 53.5 V = 3.21 kW), or the nominal 51.2 V if they can't be read. It changes when you change those settings, and charging at the current limit fills the bar. Each bar turns amber from 60% and red from 85% of its maximum, with a short status under it. On the Overview the bars sit under the power diagram (at the bottom of the page on a phone); on the TV page they are a 2×2 block at the top right. The limits are `WARN_AT`, `DANGER_AT` and the grid bar's `GAUGE_GRID_MAX_W` in `web/frontend/src/lib/gauge.ts`.

Live values arrive over Server-Sent Events every ~10 s. If SSE fails, the page falls back to polling (`?transport=poll` forces polling). The status pill turns red when the newest point is older than 45 s.

| Env var | Default | |
|---|---|---|
| `WEB_PORT` | `8088` | Host port |
| `WEB_BASIC_AUTH` | empty | `user:pass` turns on HTTP Basic auth (`/healthz` stays open for the Docker healthcheck) |
| `SITE_NAME` | empty | Name of this installation: dashboard title, browser tab and a prefix on Telegram alerts |
| `WEB_AUTH_SETTINGS_ONLY` | empty | `true` = only `/api/settings*` needs the password, so the dashboard is open and the browser asks for the username and password when you open the Settings tab. Needs `WEB_BASIC_AUTH`. |
| `TARIFF_*` | empty | Starting electricity prices; change them later on the Settings page (see below) |
| `INFLUX_*`, `TZ` | shared | Same values as the poller. `TZ` sets the day boundaries for daily kWh |

**Electricity cost.** Set `TARIFF_DAY` (and, for a two-zone meter, `TARIFF_NIGHT`, `TARIFF_NIGHT_START`, `TARIFF_NIGHT_END`, `TARIFF_CURRENCY`) in `.env` and the Overview shows today's and this month's grid cost, the tariff zone that is active now (from the server's clock) and a day-zone / night-zone table with each zone's kWh and cost. The card's **History** link opens a page for any month since the first stored reading (`?view=cost&month=2026-10&day=2026-10-18`): month totals, a stacked bar chart of every day (night zone and day zone, in cost or kWh), a table of all days, and, for a selected day, the day/night split with its cost, plus the house consumption, export, PV and battery energy. Day and night are split from the grid-import counter every 15 minutes, with the zone hours of the tariff in force at that time.

**Changing the prices.** Use the *Electricity tariff* card on the Settings page (it needs `WEB_BASIC_AUTH`, like the other changes). A change starts at the next quarter-hour, or on a date you pick, and only prices consumption from then on: every earlier 15-minute window keeps the price and the night hours it had, so past days and months do not change (the cost page lists the prices that applied in the month). A newer save replaces changes that were scheduled to start later. The currency stays the one in `.env`. The `TARIFF_*` values in `.env` are the prices before your first change; after that the saved list (`/data/tariff.json` in the `web-data` Docker volume) is the source and `.env` is ignored. Each site keeps its own. To start over, delete that file and restart the web service.

MCP (agents): `/mcp`, see [Hermes Agent](#hermes-agent-read-only-mcp). API (GET): `/api/live`, `/api/stream` (SSE), `/api/history?fields=a,b&range=6h&every=auto&agg=mean`, `/api/energy/daily?days=30`, `/api/energy/summary`, `/api/cost/history?month=2026-10`, `/api/events?days=30`, `/api/devices`, `/api/settings`, `/api/settings/log`, `/healthz`. `POST /api/settings` changes a setting.
Ranges: `1h 3h 6h 12h 24h 2d 7d 30d`. `every=auto` keeps each series at 500 points or fewer.

**Language:** the dashboard is available in English and Ukrainian. The **EN / УК** button in the page header switches it and remembers the choice in the browser. Without a saved choice it follows the browser or TV language, and `?lang=uk` / `?lang=en` in the URL forces one. Fault and alarm names from the inverter are translated too; a name with no translation is shown in English. Telegram alerts are not translated (yet). To add a string: wrap it in `t('English text')` and add the Ukrainian text to `web/frontend/src/lib/i18n.uk.ts` (a test fails if one is missing).

Code: `web/backend` (FastAPI) and `web/frontend` (Vite + Preact + uPlot). `web/Dockerfile` builds both into one image.

## Settings

The Settings page reads the inverter's settings live, and can change these:

| Setting | Register | Notes |
|---|---|---|
| Grid charging allowed / forbidden | 43110 bit 5 | 1 = allowed (confirmed on this unit; the 2020 protocol PDF says the opposite) |
| Time-of-Use mode on/off | 43110 bit 1 | |
| Reserve battery mode on/off | 43110 bit 4 | keeps the battery at the backup SOC for outages |
| Over-discharge SOC | 43011 | normal discharging stops here; must be above force-charge SOC (43018) and below max charge SOC (43010) |
| Backup (reserve) SOC | 43024 | between over-discharge SOC and max charge SOC |
| Max charge / discharge current | 43117 / 43118 | 0 A discharge = the battery won't power loads |
| Time-of-Use slots, 6 charge + 6 discharge | 43707 (enable bits), 43708–43791 | start/end (may cross midnight), current, target SOC; slot currents are bounded by the same `MAX_*_CURRENT_A` |
| Inverter clock | 43000–43005 | "Sync to server time" writes the server's local time (`TZ`) |

The **Battery hold** card explains what the charge slots are doing: if an enabled charge slot covers most of the day and grid charging is allowed, the inverter keeps the battery at that slot's target, and the small top-ups every few hours (when the charge sags by 1–2%) are normal. It suggests how to stop them (night-only slot plus Reserve battery mode).

The page also shows read-only values: other SOC floors, rated capacity, charge voltages, and all storage-mode flags. Off-grid and feed-in priority are deliberately not switchable here: switching one by mistake could cut the grid connection.

Every write except 43110/43117/43118 uses a multi-register write (FC16), because newer Solis firmware silently ignores some single-register writes. Register addresses were checked against this inverter with read-only probes (this unit uses the six-slot Time-of-Use layout). They were not tested by writing, so make the first change of each kind while watching the inverter display or SolisCloud.

The poller stays the only Modbus client, and it follows the protocol's limits: at most 50 registers per request and at least 300 ms between requests. The web app forwards changes to the poller's internal API (`:8780`, Docker network only, never published), authenticated with `INFLUX_TOKEN`.

**Changes are refused until you finish these steps:**

1. **Turn on a password**: set `WEB_BASIC_AUTH=user:pass`. Without it the page is view-only. Add `WEB_AUTH_SETTINGS_ONLY=true` to keep the rest of the dashboard open and be asked for the password only when you open Settings.
2. **Verify the current scale.** Open Settings and compare *Max charge current* with the inverter display (Advanced settings → Battery → max charge current):
   - display 100 A, page 100 A (raw 1000) → `CURRENT_SCALE=0.1` (the default)
   - display 100 A, page 10 A (raw 100) → set `CURRENT_SCALE=1`
3. **Set hard limits** no higher than your battery/BMS rating, e.g. `MAX_CHARGE_CURRENT_A=100`, `MAX_DISCHARGE_CURRENT_A=100`. Values above these are refused, and `0` refuses changes to that setting.
4. Set `CONTROL_ENABLED=true`, then run `docker compose up -d`.
5. **Test with a harmless change** while watching the inverter display, e.g. limit charging to 30 A, then set it back.

How a change works:
- You enter a value. The page shows a summary such as "Max charge current: 100 A → 30 A", and you click Apply.
- One change runs at a time, with at least `MIN_WRITE_INTERVAL_S` (default 60 s) between changes.
- The poller writes the register, waits 3 s and reads it back. If the inverter didn't keep the value, the page says so. Some Solis firmwares only accept certain settings from the front panel or SolisCloud. In that case, don't keep retrying.
- Every change is logged to InfluxDB (measurement `solis_control`), shown under *Recent changes*, and logged in `docker compose logs poller`.

Notes:
- Forbidding grid charging doesn't stop the battery charging from PV.
- Whether grid charging actually happens also depends on the work mode and any Time-of-Use windows set on the inverter.
- The register numbers come from community documentation for Solis hybrids. Step 5 is how you confirm them on your unit.

## Android TV app

`?view=tv` is a wall-display page on one screen with no scrolling: the power-flow diagram (big, top left) with the Battery card under it (charge bars, voltage, current, SOH and the backup-runtime estimate), and on the right the four gauges as a 2×2 block of bars (Grid, Backup load, Battery charge, Battery discharge), a tall Power flows chart and a strip with today's energy. Left/right on the remote (or arrow keys) change the chart range. It only reads data, so it works with `WEB_AUTH_SETTINGS_ONLY=true` and no password.

`android/` is a small app that opens this page full screen (a WebView, plus a screen for the host and port). It needs only Docker to build:

```bash
sh android/build.sh          # -> android/dist/solis-tv.apk (first build downloads the Android SDK, a few minutes)
adb connect <tv-ip>:5555     # enable Developer options -> USB/Network debugging on the TV first
adb install -r android/dist/solis-tv.apk
```

Or copy the APK to the TV with a USB stick and open it with a file manager (allow "install unknown apps"). On first start the app asks for the host and port of the dashboard (for example `192.168.1.50` and `8088`). Later, hold OK (or press Menu) to change them. If the dashboard can't be reached, the app shows Retry / Settings and retries every 15 s, so it recovers when the TV boots before the network is up.

The app talks plain HTTP (the dashboard has no TLS), so only use it on your own network. It keeps the screen on and reloads the page every 6 hours. The signing key is created in `android/.keystore/` (not committed): keep it, or the next build won't install over the old one.

## Second site on a Raspberry Pi

A second inverter at another place runs its own copy of this stack on a Raspberry Pi (Pi 4 or 5, 4 GB or more, Ubuntu Server 24.04 LTS 64-bit or 64-bit Raspberry Pi OS Lite, ideally an SSD or a good USB drive instead of an SD card). Each site keeps its own database, so a dropped link loses nothing. You reach it over Tailscale at `http://<pi-tailscale-name>:8088`. Set `SITE_NAME` in `.env`: it becomes the dashboard title and prefixes Telegram alerts (`[Cottage] ⚡ Power outage …`), so one chat can serve both sites. The TV app can show either site: change its address in the app.

Prepare the Pi where you are now, then move it:

1. In Raspberry Pi Imager choose *Other general-purpose OS → Ubuntu → Ubuntu Server 24.04 LTS (64-bit)* and use *Edit settings* to set a host name (e.g. `cottage-solis`), a user and password (or your SSH key), enable SSH, and set your time zone. Use an Ethernet cable at the site: Imager can store only one Wi-Fi network, and you can't fix Wi-Fi remotely. The first boot takes a few minutes (cloud-init); find the Pi with `ssh <user>@<host>.local` or in your router.
2. If your repo is private, either clone it on the Pi with your own credentials (an HTTPS clone with a GitHub token, or `ssh -A` from your laptop with the key loaded in your agent) and run `bash ~/solis-monitor/deploy/pi/setup.sh "Cottage"`, or just copy the one script over (`scp deploy/pi/setup.sh <user>@<pi>:`, then `bash setup.sh "Cottage"`) and let it clone with its own deploy key. Either way it installs Docker (with buildx) and Tailscale (you approve the login link in a browser), adds a read-only deploy key (you paste it into GitHub), clones the repo, writes a `.env` with fresh random secrets, turns on the 3-minute auto-deploy like the Umbrel box, and does the first build (10–20 minutes). It prints a generated Settings password; it is also in `.env` as `WEB_BASIC_AUTH`.
3. At the site: plug the Pi into the router, SSH in over Tailscale, and run `~/solis-monitor/deploy/pi/find-inverter.sh`. Do **not** run it at home: it would find your home inverter, which allows only one Modbus client (it asks you to confirm). It stops the poller, scans the LAN for a Modbus TCP device on port 502, saves it as `INVERTER_HOST` and restarts the poller. If it finds several or none, it says what to check; you can also edit `INVERTER_HOST` in `.env` and run `docker compose up -d poller`.
`~/solis-monitor/deploy/pi/status.sh` prints a one-screen health check (network, containers, configured inverter, last deploy, last poller log lines) when you're logged in remotely.
4. Settings changes start **off** (`CONTROL_ENABLED=false`). Before turning them on, verify `CURRENT_SCALE` against the inverter's display and set `MAX_*_CURRENT_A` (see Settings above). You can't see that inverter, so be extra careful.
5. For alerts, add `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` to the Pi's `.env` (the same bot works) and run `docker compose up -d poller`. Leave them empty until the Pi is at the site, or you'll get "inverter offline" alerts.

Both sites deploy from `main`, so a push updates both. A failed build or test stops at the build and leaves the old version running (the Pi sends the Telegram notice). To keep Tailscale tight, use an ACL so only your own devices reach the Pi, and don't publish its port to the internet.

## Tests

```bash
# poller control logic and device decoding (fake inverter, real register dump)
cd poller && uv run --no-project --python 3.12 --with-requirements requirements-dev.txt pytest

# web backend (pytest, InfluxDB and poller mocked)
cd web/backend && uv run --no-project --python 3.12 --with-requirements requirements-dev.txt pytest

# frontend (flow direction / dead-band logic, settings validation)
cd web/frontend && npm ci && npm test
```

Frontend development with hot reload: run `npm run dev` in `web/frontend`. It proxies `/api` to `API_URL`, which defaults to `http://localhost:8088`.

## Hermes Agent (read-only MCP)

The web service has a read-only MCP server at `http://<umbrel-ip>:8088/mcp` for agents like Hermes. Its tools:

| Tool | Answers |
|---|---|
| `get_status` | Live values, grid connected or out, battery state, and backup runtime at the current load |
| `get_history` | Any measurement over the last 1–720 h (≤ 120 points, plus min/max/mean) |
| `get_outages_and_alarms` | Outages (duration, SOC before → after) and alarms for the last N days |
| `get_energy` | kWh today / yesterday / month / year / lifetime, and cost if a tariff is set |
| `get_devices` | Inverter, battery/BMS, backup output and meter details |
| `get_schedule` | Grid charging, modes, SOC and current limits, enabled Time-of-Use slots, inverter clock |

There are no tools that change anything. Settings are changed only on the dashboard (password + confirmation).

1. Generate a token and put it in `.env` as `MCP_TOKEN=` (`openssl rand -hex 32`), then run `docker compose up -d web`. The token is separate from `WEB_BASIC_AUTH` on purpose: the dashboard password can change inverter settings, so the agent shouldn't have it.
2. In Hermes's `config.yaml`, replace any old `solis` entry (the old `:8765` server no longer exists) with:
   ```yaml
   mcp_servers:
     solis:
       url: "http://<umbrel-ip>:8088/mcp"      # the IP; .local names may not resolve inside the app
       headers:
         Authorization: "Bearer <MCP_TOKEN>"
       timeout: 60
   ```
   On Umbrel the file is in the Hermes app's data folder: `sudo find ~/umbrel/app-data/hermes-agent -name config.yaml`. Restart Hermes; the tools appear as `mcp_solis_*`.
3. Try asking: *"What's the battery at, and how long would it last if the power went out now?"*

## Telegram alerts

The poller sends Telegram messages for:
- a grid outage starting (battery % and estimated runtime) and ending (duration, battery before → after)
- the battery crossing `ALERT_SOC_LEVELS` (default 50/30/20 %) during an outage
- inverter and BMS alarms starting and clearing
- the inverter being unreachable for `ALERT_OFFLINE_MIN` minutes (default 5), and coming back

`deploy/deploy.sh` also reports each deploy and each failed deploy.

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`, then run `docker compose up -d poller`. You can reuse the bot Hermes already uses: sending messages doesn't interfere with Hermes reading them. For a private chat, the chat ID is your Telegram user ID (send any message to @userinfobot to get it). Send yourself a test:

```bash
docker exec solis-poller python notify.py --test
```

Alerts are sent from a background queue with retries, so a slow or unreachable Telegram never delays polling. Hermes doesn't see these messages as conversation; ask it (it reads the same data through MCP).

## Sign conventions

| Field | Meaning |
|---|---|
| `grid_power` | **+** importing from grid, **−** exporting |
| `battery_power_signed` | **+** charging, **−** discharging |
| `total_load_power` | house (grid-side) + backup/EPS load |

Raw registers are stored too (`meter_power`, `battery_power`, `battery_direction`, …).

## Common changes

- **Poll rate / inverter IP / Modbus unit id**: edit `.env`, then `docker compose up -d`.
- **Retention**: `INFLUX_RETENTION` only applies on the very first start. At 10 s polling, a year of data is roughly 100–200 MB.
- **Add a register**: add a line to `REGISTERS` in `poller/poller.py` and to `FIELDS` in `web/backend/app/validation.py`, then `docker compose up -d --build`.

## Security

- The inverter's Modbus TCP has **no authentication**: anything on your LAN can write to it. Don't port-forward 502, and keep guest Wi-Fi isolated.
- Keep 8088 LAN-only. Use Tailscale/WireGuard for remote access, never port forwarding.
- InfluxDB is not reachable from outside Docker. To use its UI or CLI, run `docker exec -it solis-influxdb influx ...`, or temporarily add a `ports:` entry.

### Rotating the InfluxDB token

`INFLUX_TOKEN` only sets the admin token on the very first start; changing it later in `.env` locks the poller and web service out. To rotate it while keeping the data:

```bash
docker exec solis-influxdb influx auth create --org home --operator --description "solis stack"   # copy the Token
# put it in .env as INFLUX_TOKEN, then:
docker compose up -d
docker exec solis-influxdb influx config set --config-name default --token <new token>
docker exec solis-influxdb influx auth list                    # find the old token's ID
docker exec solis-influxdb influx auth delete --id <old ID>
```

The same value also authenticates the web service to the poller's internal API, so both pick it up from `.env`.
- Basic auth sends the password unencrypted over plain HTTP. That's acceptable on your own LAN; for remote access, use the VPN.
- Changing settings refuses cross-site requests (JSON body only, plus Origin/Sec-Fetch-Site checks), so another website can't make your browser apply a change.

## Stop / reset

```bash
docker compose down        # stop, keep data
docker compose down -v     # stop and DELETE all stored data
```

## Disclaimer and license

Independent project, not affiliated with or endorsed by Solis / Ginlong. It reads (and, if you enable it, writes) a real inverter over Modbus; only the S6-EH1P register map has been checked against hardware, and other models may differ. Use it at your own risk. Released under the [MIT License](LICENSE).
