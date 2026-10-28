#!/usr/bin/env python3
"""Checks the Umbrel community-store package against Umbrel's packaging rules and against the main docker-compose.yml.

    python3 deploy/tests/check-umbrel-app.py [--check-images]      (needs PyYAML; --check-images asks the registries)

The drift check matters most: when docker-compose.yml gains an environment variable or a mount for the poller or the
web service, the Umbrel compose must get it too, or the app would silently run without it.
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
problems = []


def check(ok, message):
    if not ok:
        problems.append(message)


def load(path):
    return yaml.safe_load(Path(path).read_text())      # strictly ONE document: umbrelOS rejects the whole store if umbrel-app-store.yml has a stray ---


store = load(ROOT / "umbrel-app-store.yml")
check(re.fullmatch(r"[a-z]+(-[a-z]+)*", str(store.get("id", ""))), "store id must be lowercase letters and dashes")
check(store.get("name"), "the store needs a name")
check(set(store) == {"id", "name"}, "umbrel-app-store.yml holds just id and name")

apps = [p for p in ROOT.iterdir() if p.is_dir() and (p / "umbrel-app.yml").exists()]
check(len(apps) >= 1, "no app directory with an umbrel-app.yml found")
ports = {}
for app in apps:
    m = load(app / "umbrel-app.yml")
    c = load(app / "docker-compose.yml")
    exports = (app / "exports.sh").read_text() if (app / "exports.sh").exists() else ""
    app_id = m["id"]
    name = f"[{app.name}]"

    # ---- manifest
    check(app.name == app_id, f"{name} the directory name must equal the manifest id ({app_id})")
    check(app_id.startswith(store["id"] + "-"), f"{name} the id must start with the store id '{store['id']}-'")
    for key in ("manifestVersion", "name", "tagline", "category", "version", "description", "developer", "website", "repo", "support", "port", "path"):
        check(key in m, f"{name} umbrel-app.yml lacks `{key}`")
    check(re.fullmatch(r"\d+\.\d+\.\d+", str(m.get("version", ""))), f"{name} version must be x.y.z, got {m.get('version')!r}")
    check(isinstance(m.get("port"), int) and not 40000 <= m["port"] <= 49999, f"{name} port must be an int outside 40000-49999")
    check(m.get("port") not in ports, f"{name} port {m.get('port')} is used by {ports.get(m.get('port'))}")
    ports[m.get("port")] = app.name
    check(str(m.get("icon", "")).startswith("https://") and (app / "icon.svg").exists(), f"{name} needs an https icon URL and icon.svg")
    check(all(str(g).startswith("https://") for g in m.get("gallery", [])), f"{name} gallery images must be https URLs")
    check(m.get("storage") == {"dataRoot": "data"}, f"{name} storage must be {{dataRoot: data}}")
    check(m.get("deterministicPassword") is False or "${APP_PASSWORD}" in (app / "docker-compose.yml").read_text(),
          f"{name} deterministicPassword is true but the compose file never uses ${{APP_PASSWORD}}")
    check(m.get("dependencies") == [], f"{name} dependencies must be a list of other Umbrel app ids")

    # ---- compose
    services = c.get("services", {})
    check("app_proxy" in services, f"{name} compose needs an app_proxy service")
    env = services.get("app_proxy", {}).get("environment", {})
    host = str(env.get("APP_HOST", ""))
    match = re.fullmatch(rf"{re.escape(app_id)}_(.+)_1", host)
    check(match and match.group(1) in services, f"{name} APP_HOST must be <app-id>_<service>_1 of a service in this file, got {host!r}")
    check(isinstance(env.get("APP_PORT"), int), f"{name} APP_PORT must be the web service's internal port")
    check("volumes" not in c and "networks" not in c, f"{name} no top-level volumes or networks (use bind mounts under ${{APP_DATA_DIR}})")
    used_vars = set(re.findall(r"\$\{(APP_[A-Z0-9_]+)", (app / "docker-compose.yml").read_text()))
    builtin = {"APP_DATA_DIR", "APP_PASSWORD", "APP_SEED", "APP_VERSION", "APP_ID", "APP_DOMAIN", "APP_PROXY_PORT"}
    for var in sorted(used_vars - builtin):
        check(f"export {var}=" in exports, f"{name} compose uses ${{{var}}} but exports.sh never exports it")
    for svc, s in services.items():
        if svc == "app_proxy":
            continue
        where = f"{name} service {svc}:"
        img = str(s.get("image", ""))
        check(re.search(r"@sha256:[0-9a-f]{64}$", img), f"{where} image must be pinned by digest (name:tag@sha256:...), got {img!r}")
        check(":latest" not in img, f"{where} never :latest")
        check("build" not in s and "container_name" not in s and "ports" not in s, f"{where} no build, container_name or ports")
        check(not s.get("privileged") and not any("docker.sock" in str(v) for v in s.get("volumes", [])), f"{where} no privileged mode or docker socket")
        check(s.get("restart") == "on-failure", f"{where} restart must be on-failure")
        for v in s.get("volumes", []):
            src = str(v).split(":")[0]
            check(src.startswith("${APP_DATA_DIR}/data/"), f"{where} volume {v!r} must be a bind mount under ${{APP_DATA_DIR}}/data")
            check((app / src.replace("${APP_DATA_DIR}/", "") / ".gitkeep").exists(), f"{where} commit an empty directory with .gitkeep for {src}")
    web_tag = re.search(r"solis-monitor-web:(v[\d.]+)@", str(services.get("web", {}).get("image", "")))
    poller_tag = re.search(r"solis-monitor-poller:(v[\d.]+)@", str(services.get("poller", {}).get("image", "")))
    check(web_tag and poller_tag and web_tag.group(1) == poller_tag.group(1) == "v" + str(m.get("version")),
          f"{name} the poller and web image tags must both be v{m.get('version')}, the manifest version")

    # ---- no drift from the main docker-compose.yml (only for the solis-monitor app)
    main = load(ROOT / "docker-compose.yml")["services"]
    ignored_env = {"web": {"SOLIS_REPO", "UPDATE_DIR"}, "poller": set()}      # the self-update bridge does not exist on Umbrel
    for svc in ("poller", "web"):
        mine = set((services.get(svc, {}).get("environment") or {}))
        theirs = set(main[svc]["environment"]) - ignored_env[svc]
        check(theirs <= mine, f"{name} {svc}: the main docker-compose.yml sets {sorted(theirs - mine)} but the Umbrel compose does not")
        mounts = lambda s: {str(v).split(":")[1] for v in s.get("volumes", [])}
        extra = {"/control"} if svc == "web" else set()
        check(mounts(main[svc]) - extra <= mounts(services.get(svc, {})),
              f"{name} {svc}: the main compose mounts {sorted(mounts(main[svc]) - extra)} but the Umbrel compose mounts {sorted(mounts(services.get(svc, {})))}")
    check({str(v).split(":")[1] for v in main["influxdb"]["volumes"]} == {str(v).split(":")[1] for v in services["influxdb"]["volumes"]},
          f"{name} influxdb: different container paths than the main compose")

    # ---- optional: do the pinned digests exist, for both architectures?
    if "--check-images" in sys.argv:
        sys.path.insert(0, str(ROOT / "deploy" / "umbrel"))
        import importlib.util
        spec = importlib.util.spec_from_file_location("update_app", ROOT / "deploy" / "umbrel" / "update-app.py")
        ua = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ua)
        for svc in ("poller", "web"):
            tag, digest = re.search(r":(v[\d.]+)@(sha256:\w+)", services[svc]["image"]).groups()
            got, plats = ua.ghcr(svc, tag)
            check(got == digest and {"amd64", "arm64"} <= set(plats), f"{name} {svc}: pinned {digest} but the registry has {got} ({plats})")
        digest = re.search(r"influxdb:2\.7@(sha256:\w+)", services["influxdb"]["image"]).group(1)
        got, plats = ua.hub("influxdb", "2.7")
        print(f"note: influxdb:2.7 is pinned to {digest[:19]}…; the registry's newest is {got[:19]}…" if got != digest else "influxdb digest is current")
        check({"amd64", "arm64"} <= set(plats), f"{name} influxdb: not multi-arch")

if problems:
    print("Umbrel package problems:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print(f"Umbrel package OK ({', '.join(a.name for a in apps)})")
