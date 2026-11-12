"""The API, one router per concern: data (read-only, from InfluxDB and the poller's caches), settings (changes,
all behind the password and same-origin checks) and updates (the dashboard's release check and update request).
Each module has a build(ctx) that returns its APIRouter; main.create_app wires them together."""
