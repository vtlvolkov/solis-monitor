# Advanced: building from source and a second site

Everything here is optional. The [quick install](../README.md#install) needs none of it.

## Build from source

To build the images yourself, for example to change the code:

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

## A second site

A second inverter at another place gets its own install, made with the same one-command installer as the first (a Raspberry Pi 4 or 5 with 64-bit Raspberry Pi OS or Ubuntu Server works well, ideally with an SSD or a good USB drive instead of an SD card). Each site keeps its own database, so a dropped link between them loses nothing.

1. Install **at the site**, on the same network as that inverter's data logger, so the installer finds the right one. Don't run it at home: it would find your home inverter, which allows only one Modbus client. Give the site a name: `curl -fsSL https://github.com/vtlvolkov/solis-monitor/releases/latest/download/install.sh | sudo bash -s -- --site-name Cottage`. The name becomes the dashboard title and prefixes Telegram alerts (`[Cottage] ⚡ Power outage …`), so one chat can serve both sites.
2. For remote access, install [Tailscale](https://tailscale.com) on it (`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`) and open `http://<tailscale-name>:8088`. Use an ACL so only your own devices reach it, and never forward its port to the internet. Use an Ethernet cable if you can: you can't fix Wi-Fi remotely.
3. Settings changes start **off** (`CONTROL_ENABLED=false`). Before turning them on with `solis-monitor edit`, verify `CURRENT_SCALE` against the inverter's display and set `MAX_*_CURRENT_A` (see [Changing inverter settings](settings.md)). You can't see that inverter, so be extra careful.
4. For alerts, add `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` with `solis-monitor edit` (the same bot works for both sites).

The TV app can show either site: change its address in the app. Each site updates on its own (`solis-monitor update`, from the dashboard, or nightly with `solis-monitor auto-update on`), so you can try a new release on one site first.

## Maintenance

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

The dashboard does not use this token when `INFLUX_READ_TOKEN` is set (see below), and the poller's internal API has its own secret (`CONTROL_TOKEN`), so neither needs changing.

### The dashboard's tokens

The dashboard only ever reads the database, so `install.sh` gives it a read-only InfluxDB token (`INFLUX_READ_TOKEN`) and a secret of its own for the poller's internal API (`CONTROL_TOKEN`) instead of the admin token. An install made before these existed keeps working with `INFLUX_TOKEN` for both; `solis-monitor status` says so, and one command adds them:

```bash
solis-monitor secure-tokens      # makes the read-only token in the running database, writes both to .env, restarts poller and web
```

Building from source with your own `.env`: run `docker exec solis-influxdb influx bucket list --name solis` for the bucket ID, then `docker exec solis-influxdb influx auth create --org home --read-bucket <ID> --description dashboard`, put the token in `.env` as `INFLUX_READ_TOKEN`, set `CONTROL_TOKEN` to any long random string, and `docker compose up -d`. A reset database (`docker compose down -v`) no longer knows the read-only token: clear `INFLUX_READ_TOKEN` and run `secure-tokens` again.

### Backup and restore

`solis-monitor backup` writes one `.tar.gz` (in `/opt/solis-monitor/backups/`, or the file or folder you name) with the InfluxDB history (an online `influx backup`, nothing stops), the files saved from the dashboard (logger address, battery capacity, outages in progress, tariff versions, a changed password), `.env` and the compose file. It holds every password and token, so it is created readable by root only; copy it somewhere else than this machine, because a dead SD card is the reason to have it (for example `solis-monitor backup /mnt/usb/`, or a cron line that runs it and rsyncs the folder).

**From the dashboard:** **Settings → Backup** has a **Back up now** button and the list of archives on the server with a **Download** link for each. The dashboard cannot read the database's files itself: the button asks the same small helper on the server that the updater uses (`solis-monitor dashboard-updates on` sets both up; the installer does this), which runs `solis-monitor backup --from-dashboard` and writes the archive into `/opt/solis-monitor/backups/`, mounted read-only into the web container for the list and the downloads. The server keeps the newest five made this way; archives made by hand elsewhere are never touched. A download carries `.env`, so it sits behind the Settings login like every change.

The tab's **Folder** row sets where the archives go (and how many to keep there, 1–50): for example a mounted USB drive or network share, so the backup leaves the machine by itself. The browser only asks; the helper on the server checks the folder before using it: it must exist, be writable, and sit under `/mnt`, `/media`, `/srv`, `/home`, `/opt`, `/var/backups`, `/backup`, `/backups` or `/data` (other roots, such as a NAS's `/volume1`, go in `SOLIS_BACKUP_ROOTS` in `.env`, separated by colons). `solis-monitor backup` without a path uses the same folder. Archives in a folder other than the server's own `backups/` are listed on the tab (from an index the helper writes after each backup) but not downloadable: copy them from the server or the drive. The ✕ next to an archive removes it (after a confirmation), through the same helper. **Restore…** next to an archive restores it from the dashboard: by default the helper first makes a backup of the current state (so a wrong restore can be undone by restoring that one), then does the same as `solis-monitor restore FILE`; you type `restore` to confirm. The dashboard restarts during it and comes back with the backup's settings — including the backup's password, if it was different.

`solis-monitor restore FILE` puts one back, on the same install or on a fresh one made with the installer (any inverter address or password you typed there is replaced by the backup's). It stops the poller and the dashboard, restores the database in full (`influx restore --full`, so the tokens in the restored `.env` match it), takes `.env` from the backup except the installer's own lines (which release is installed and from where), puts the saved files back and starts everything again. Nothing from before the restore is kept.

The Umbrel app is backed up by umbrelOS together with its other apps (`app-data/vtlvolkov-solis-monitor/data`); these commands are for installer-based installs.

### Stop or reset

```bash
docker compose down        # stop, keep data
docker compose down -v     # stop and DELETE all stored data
```
