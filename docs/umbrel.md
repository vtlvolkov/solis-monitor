# Solis Monitor on Umbrel

This repository is also an [Umbrel community app store](https://github.com/getumbrel/umbrel-community-app-store). It holds one app, **Solis Monitor**, which runs the same prebuilt images as the [installer](../README.md#install) with its data in umbrelOS's app folder, so umbrelOS installs, backs up, updates and removes it like any other app.

## Install

1. In umbrelOS open the **App Store**, then the **⋯** menu → **Community App Stores**, and add
   `https://github.com/vtlvolkov/solis-monitor`
2. Open the new **Volkov App Store**, choose **Solis Monitor** and install it.
3. Open the app and tell it where the inverter's data logger is: **Settings** (the login is on the app's page in umbrelOS) → **General** → **Inverter connection**. Type the logger's IP address (your router's device list shows it), or press **Find inverter** to search your network, pick the result with **Use**, and **Save**. The poller follows within seconds; no restart. (The settings file `~/umbrel/app-data/vtlvolkov-solis-monitor/data/config/settings.env` also has `INVERTER_HOST`, as the first address; an address saved in the dashboard wins over it. The file holds the other options too: time zone, site name, Telegram alerts, electricity prices, battery capacity. Restart the app after editing it.)
4. Within half a minute the pill in the top right says **Online**.

The dashboard login for the Settings page is shown on the app's page in umbrelOS (user `admin`). You can change it in the dashboard (Settings → System → Dashboard login); umbrelOS keeps showing the original, which then stops working. To go back to the original, use the card's "Use the original password again", or if you forgot yours: `docker exec vtlvolkov-solis-monitor_web_1 rm -f /data/auth.json`. The rest of the dashboard is open on your home network on `http://umbrel.local:8188`, so the [Android TV app](tv-app.md) can show it (host `umbrel.local`, port `8188`). umbrelOS's own sign-in is switched off for this app for the same reason.

**Only one program may talk to the logger.** Stop anything else that reads it (Home Assistant, another monitor, the installer-based stack on the same box) before you start the app.

## Updating

umbrelOS updates the app (App Store → Updates). The Software card on the Settings page shows the version and says so; its own update button belongs to the installer-based setup and is not used here.

## Moving an existing installation (experimental)

The history lives in InfluxDB. To keep it, copy the old database folder into the app and give the app the credentials that database was created with. Back up first, and stop the old stack, because two programs cannot share the logger.

```bash
# 1. stop the old stack (installer-based: cd /opt/solis-monitor && sudo docker compose down)
# 2. copy its database into the app's folder (the volume name depends on the old folder, here solis-monitor):
sudo cp -a /var/lib/docker/volumes/solis-monitor_influxdb-data/_data/. ~/umbrel/app-data/vtlvolkov-solis-monitor/data/influxdb/
sudo cp -a /var/lib/docker/volumes/solis-monitor_influxdb-config/_data/. ~/umbrel/app-data/vtlvolkov-solis-monitor/data/influxdb-config/
# 3. put the old INFLUX_PASSWORD and INFLUX_TOKEN from the old .env into settings.env, then restart the app
```

The tariff history (`web-data`) can be copied the same way into `data/web/`. This path has not been tested on real hardware yet; if something looks wrong, nothing is lost as long as you keep the old volumes until you are happy.

## For maintainers

- Layout: `umbrel-app-store.yml` (store id `vtlvolkov`), and `vtlvolkov-solis-monitor/` with `umbrel-app.yml`, `docker-compose.yml`, `exports.sh` (secrets and the settings file), `hooks/pre-start` (writes the settings file the first time) and `icon.svg`.
- **After every release**, point the app at it and push: `deploy/umbrel/update-app.py vX.Y.Z --notes "what changed"` pins the two images by their multi-arch digests and bumps `version`; umbrelOS offers the update once it sees the new version on `main`. Add `--refresh-influx` to re-pin InfluxDB.
- `deploy/tests/check-umbrel-app.py` checks the package against Umbrel's rules (digest-pinned images, bind mounts only, `APP_HOST`, ports, exported variables) and against the main `docker-compose.yml`: a variable or mount added there must be added to the Umbrel compose, or the check fails. `deploy/tests/test-umbrel-app.sh` tests `exports.sh` and the hook. Both run in CI.
- Environment of the web service: `UPDATES_MANAGED_BY=umbrelOS` turns off the dashboard's own update check and button.
