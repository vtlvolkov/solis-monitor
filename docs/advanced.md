# Advanced: building from source, auto-deploy and a second site

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

## Auto-deploy from your own GitHub repo

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

## A second site on a Raspberry Pi

A second inverter at another place runs its own copy of this stack on a Raspberry Pi (Pi 4 or 5, 4 GB or more, Ubuntu Server 24.04 LTS 64-bit or 64-bit Raspberry Pi OS Lite, ideally an SSD or a good USB drive instead of an SD card). Each site keeps its own database, so a dropped link loses nothing. You reach it over Tailscale at `http://<pi-tailscale-name>:8088`. Set `SITE_NAME` in `.env`: it becomes the dashboard title and prefixes Telegram alerts (`[Cottage] ⚡ Power outage …`), so one chat can serve both sites. The TV app can show either site: change its address in the app.

Prepare the Pi where you are now, then move it:

1. In Raspberry Pi Imager choose *Other general-purpose OS → Ubuntu → Ubuntu Server 24.04 LTS (64-bit)* and use *Edit settings* to set a host name (e.g. `cottage-solis`), a user and password (or your SSH key), enable SSH, and set your time zone. Use an Ethernet cable at the site: Imager can store only one Wi-Fi network, and you can't fix Wi-Fi remotely. The first boot takes a few minutes (cloud-init); find the Pi with `ssh <user>@<host>.local` or in your router.
2. If your repo is private, either clone it on the Pi with your own credentials (an HTTPS clone with a GitHub token, or `ssh -A` from your laptop with the key loaded in your agent) and run `bash ~/solis-monitor/deploy/pi/setup.sh "Cottage"`, or just copy the one script over (`scp deploy/pi/setup.sh <user>@<pi>:`, then `bash setup.sh "Cottage"`) and let it clone with its own deploy key. Either way it installs Docker (with buildx) and Tailscale (you approve the login link in a browser), adds a read-only deploy key (you paste it into GitHub), clones the repo, writes a `.env` with fresh random secrets, turns on the 3-minute auto-deploy like the Umbrel box, and does the first build (10–20 minutes). It prints a generated Settings password; it is also in `.env` as `WEB_BASIC_AUTH`.
3. At the site: plug the Pi into the router, SSH in over Tailscale, and run `~/solis-monitor/deploy/pi/find-inverter.sh`. Do **not** run it at home: it would find your home inverter, which allows only one Modbus client (it asks you to confirm). It stops the poller, scans the LAN for a Modbus TCP device on port 502, saves it as `INVERTER_HOST` and restarts the poller. If it finds several or none, it says what to check; you can also edit `INVERTER_HOST` in `.env` and run `docker compose up -d poller`.
`~/solis-monitor/deploy/pi/status.sh` prints a one-screen health check (network, containers, configured inverter, last deploy, last poller log lines) when you're logged in remotely.
4. Settings changes start **off** (`CONTROL_ENABLED=false`). Before turning them on, verify `CURRENT_SCALE` against the inverter's display and set `MAX_*_CURRENT_A` (see [Changing inverter settings](settings.md)). You can't see that inverter, so be extra careful.
5. For alerts, add `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` to the Pi's `.env` (the same bot works) and run `docker compose up -d poller`. Leave them empty until the Pi is at the site, or you'll get "inverter offline" alerts.

Both sites deploy from `main`, so a push updates both. A failed build or test stops at the build and leaves the old version running (the Pi sends the Telegram notice). To keep Tailscale tight, use an ACL so only your own devices reach the Pi, and don't publish its port to the internet.

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

The same value also authenticates the web service to the poller's internal API, so both pick it up from `.env`.

### Stop or reset

```bash
docker compose down        # stop, keep data
docker compose down -v     # stop and DELETE all stored data
```
