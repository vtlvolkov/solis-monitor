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

## Updating from the dashboard (how it works)

The web container has no Docker access on purpose. **Settings → System → Software** (`web/frontend/src/components/UpdateCard.tsx`) calls `/api/settings/update*` (`web/backend/app/main.py`, logic in `updates.py`). Those endpoints sit behind the Settings login and the same same-origin checks as every other change. They only ever:

1. ask GitHub for the newest published release of `SOLIS_REPO` (on button press at most once per 30 s, and in the background every `UPDATE_CHECK_HOURS`, default 6, +-10 % jitter, ETag so an unchanged answer is free, backing off after failures; `/api/version` publishes the result for the header badge), and
2. drop a request file in `control/` (`./control` on the host, `/control` in the container).

On the host, `solis-monitor dashboard-updates on` installs a systemd path unit that watches `control/update-request` and runs `solis-monitor update --from-dashboard` (as the folder's owner, never as root for user-owned folders). The request's content is ignored: the host script finds the newest release itself, never downgrades, rolls back if the new version is unhealthy, and reports progress in `control/update-status.json` (state, message and a `percent` that moves in steps: 5 looking for the release, 15 files, 20 and 45 for the poller and web images, 72 restart, 75-98 while waiting for health, 100 done), which the page polls and draws as a progress bar. A crashed updater cannot leave the page waiting: a `running` status older than 30 minutes is shown as failed. Builds from source (empty `SOLIS_VERSION`) show the version as "built from source" and never offer an update.

## Releases and the installer

- `install.sh` (repo root) and `deploy/solis-monitor` are the installer and the management command. `deploy/tests/test-install.sh` tests both without root, Docker containers or the internet (a stand-in `docker`, a fake Modbus logger and `file://` releases); CI also runs shellcheck on them.
- Pushing a tag such as `v1.2.0` runs `.github/workflows/release.yml`: it builds and tests the poller and web images for amd64 and arm64, publishes them to `ghcr.io/<owner>/solis-monitor-{poller,web}` and builds the Android TV app (its unit tests run first) and creates the GitHub release with `install.sh` (stamped with the tag), `docker-compose.yml` and `solis-tv.apk` attached. After the first release, make the two packages public in the GitHub package settings.
- **Test release:** a tag with a suffix, such as `v1.2.0-rc.1`, builds and publishes everything the same way but as a GitHub pre-release, and its images are not tagged `latest`. Nothing picks it up by itself (nightly updates, the dashboard badge and the installer all ask for the latest release), so install it on one site with `solis-monitor update --version v1.2.0-rc.1`. When it works, tag the final `v1.2.0` and move the site to it with `solis-monitor update --version v1.2.0` (a plain `solis-monitor update` treats the final version as older than the rc and does nothing, and the badge doesn't show while an rc is installed). To abandon an rc, go back the same way: `solis-monitor update --version <previous tag>`.
- **Dry run:** after changing `release.yml` (or the actions it uses), start it by hand from the repository's Actions tab (*release → Run workflow*). It builds the images for both platforms and the APK exactly as a release would, but publishes nothing.
- **APK signing:** Android only installs an update over an app signed with the same key. Store the key once as a repository secret: `base64 -w0 android/.keystore/debug.keystore | gh secret set ANDROID_KEYSTORE_B64` (use the keystore your TV already has the app from, if any). Without the secret the release still builds, with a fresh key and a warning in the job log. The tag becomes the app version (`v1.2.3` is version code 10203).
- Keep every new compose variable optional (`${VAR:-default}`): an update replaces `docker-compose.yml` but never touches the user's `.env`.

## The Umbrel app

`vtlvolkov-solis-monitor/` is the Umbrel packaging of the same images (guide: [umbrel.md](umbrel.md)). Keep it in step with the main stack: `deploy/tests/check-umbrel-app.py` fails when `docker-compose.yml` sets an environment variable or mount for the poller or web service that the Umbrel compose lacks. After a release the `umbrel-store` job of the release workflow runs `deploy/umbrel/update-app.py vX.Y.Z` (it pins the new image digests and bumps the version) and pushes the result to `main`; umbrelOS then offers the update. If that job fails, run the script by hand and push.

