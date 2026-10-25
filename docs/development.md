# Development

```bash
# poller control logic and device decoding (fake inverter, real register dump)
cd poller && uv run --no-project --python 3.12 --with-requirements requirements-dev.txt pytest

# web backend (pytest, InfluxDB and poller mocked)
cd web/backend && uv run --no-project --python 3.12 --with-requirements requirements-dev.txt pytest

# frontend (flow direction / dead-band logic, settings validation)
cd web/frontend && npm ci && npm test
```

Frontend development with hot reload: run `npm run dev` in `web/frontend`. It proxies `/api` to `API_URL`, which defaults to `http://localhost:8088`.

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

See also `CLAUDE.md` in the repository root for an architecture overview written for coding agents.

## Releases and the installer

- `install.sh` (repo root) and `deploy/solis-monitor` are the installer and the management command. `deploy/tests/test-install.sh` tests both without root, Docker containers or the internet (a stand-in `docker`, a fake Modbus logger and `file://` releases); CI also runs shellcheck on them.
- Pushing a tag such as `v1.2.0` runs `.github/workflows/release.yml`: it builds and tests the poller and web images for amd64 and arm64, publishes them to `ghcr.io/<owner>/solis-monitor-{poller,web}` and builds the Android TV app (its unit tests run first) and creates the GitHub release with `install.sh` (stamped with the tag), `docker-compose.yml` and `solis-tv.apk` attached. After the first release, make the two packages public in the GitHub package settings.
- **APK signing:** Android only installs an update over an app signed with the same key. Store the key once as a repository secret: `base64 -w0 android/.keystore/debug.keystore | gh secret set ANDROID_KEYSTORE_B64` (use the keystore your TV already has the app from, if any). Without the secret the release still builds, with a fresh key and a warning in the job log. The tag becomes the app version (`v1.2.3` is version code 10203).
- Keep every new compose variable optional (`${VAR:-default}`): an update replaces `docker-compose.yml` but never touches the user's `.env`.

