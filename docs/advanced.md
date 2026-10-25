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

An optional way to build from `main` instead of using the releases; you do not need it for the quick install. With it, the host checks the GitHub repo every 3 minutes and redeploys when `main` has new commits (`deploy/deploy.sh`, run by a systemd timer). The Docker builds run all test suites, so a commit with failing tests stops at the build and the running version stays up. GitHub Actions (`.github/workflows/test.yml`) runs the same tests on every push. `.env` exists only on the host and is never committed.

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

The same value also authenticates the web service to the poller's internal API, so both pick it up from `.env`.

### Stop or reset

```bash
docker compose down        # stop, keep data
docker compose down -v     # stop and DELETE all stored data
```
