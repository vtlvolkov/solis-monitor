# Alerts and AI agents

## Telegram alerts

The poller sends Telegram messages for:
- a grid outage starting (battery % and estimated runtime) and ending (duration, battery before → after)
- the battery crossing `ALERT_SOC_LEVELS` (default 50/30/20 %) during an outage
- inverter and BMS alarms starting and clearing
- the inverter being unreachable for `ALERT_OFFLINE_MIN` minutes (default 5), and coming back

`solis-monitor update` also reports each update and each failed update.

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`, then run `docker compose up -d poller`. For a private chat, the chat ID is your Telegram user ID (send any message to @userinfobot to get it). Send yourself a test:

```bash
docker exec solis-poller python notify.py --test
```

Alerts are sent from a background queue with retries, so a slow or unreachable Telegram never delays polling.

## Read-only MCP server for AI agents

The web service has a read-only MCP server at `http://<server-ip>:8088/mcp` for AI agents (for example Hermes Agent). Its tools:

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
2. In your agent's MCP configuration (for Hermes: `config.yaml`) add:
   ```yaml
   mcp_servers:
     solis:
       url: "http://<server-ip>:8088/mcp"      # the IP; .local names may not resolve inside the app
       headers:
         Authorization: "Bearer <MCP_TOKEN>"
       timeout: 60
   ```
3. Try asking: *"What's the battery at, and how long would it last if the power went out now?"*
