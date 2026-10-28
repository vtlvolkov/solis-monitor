#!/usr/bin/env python3
"""Point the Umbrel app at a release: `deploy/umbrel/update-app.py v0.1.12 [--notes "...", can be several lines] [--refresh-influx]`.

Umbrel wants every image pinned by the digest of its multi-arch index, so this looks the digests up in the registries
(ghcr.io for our two images, Docker Hub for InfluxDB with --refresh-influx), checks that amd64 and arm64 are both there,
then rewrites the images in vtlvolkov-solis-monitor/docker-compose.yml and `version:` (and optionally `releaseNotes:`)
in umbrel-app.yml. Commit the result and push it: umbrelOS offers the update once it sees the new version on main.
"""
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "vtlvolkov-solis-monitor"
OWNER = "vtlvolkov"
INDEX_TYPES = "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json"


def _get(url, headers=None, head=False):
    req = urllib.request.Request(url, headers=headers or {}, method="HEAD" if head else "GET")
    return urllib.request.urlopen(req, timeout=30)


def index(host, token_url, repo, tag):
    """(digest, platforms) of a multi-arch image."""
    token = json.load(_get(token_url))["token"]
    h = {"Authorization": f"Bearer {token}", "Accept": INDEX_TYPES}
    digest = _get(f"https://{host}/v2/{repo}/manifests/{tag}", h, head=True).headers["Docker-Content-Digest"]
    manifests = json.load(_get(f"https://{host}/v2/{repo}/manifests/{tag}", h)).get("manifests", [])
    return digest, sorted({m["platform"]["architecture"] for m in manifests if m["platform"]["architecture"] != "unknown"})


def ghcr(name, tag):
    repo = f"{OWNER}/solis-monitor-{name}"
    return index("ghcr.io", f"https://ghcr.io/token?scope=repository:{repo}:pull", repo, tag)


def hub(name, tag):
    return index("registry-1.docker.io", f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/{name}:pull",
                 f"library/{name}", tag)


def need_both(what, platforms):
    if not {"amd64", "arm64"} <= set(platforms):
        sys.exit(f"{what} is not built for both amd64 and arm64 (found: {', '.join(platforms) or 'nothing'})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tag", help="release tag, e.g. v0.1.12")
    ap.add_argument("--notes", help="releaseNotes for umbrelOS's update dialog (default: keep as is)")
    ap.add_argument("--refresh-influx", action="store_true", help="also re-pin influxdb:2.7 to its newest digest")
    a = ap.parse_args()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", a.tag):
        sys.exit("the tag must look like v1.2.3 (test releases with a suffix are not for the store)")
    version = a.tag[1:]

    compose = (APP / "docker-compose.yml").read_text()
    for name in ("poller", "web"):
        digest, platforms = ghcr(name, a.tag)
        need_both(f"solis-monitor-{name}:{a.tag}", platforms)
        compose, n = re.subn(rf"(image: ghcr\.io/{OWNER}/solis-monitor-{name}:)\S+", rf"\g<1>{a.tag}@{digest}", compose)
        if n != 1:
            sys.exit(f"expected one {name} image line in docker-compose.yml, found {n}")
        print(f"{name}: {a.tag}@{digest}  ({', '.join(platforms)})")
    if a.refresh_influx:
        digest, platforms = hub("influxdb", "2.7")
        need_both("influxdb:2.7", platforms)
        compose, n = re.subn(r"(image: influxdb:2\.7)@sha256:[0-9a-f]{64}", rf"\g<1>@{digest}", compose)
        if n != 1:
            sys.exit(f"expected one influxdb image line in docker-compose.yml, found {n}")
        print(f"influxdb: 2.7@{digest}  ({', '.join(platforms)})")
    (APP / "docker-compose.yml").write_text(compose)

    manifest = (APP / "umbrel-app.yml").read_text()
    manifest, n = re.subn(r'(?m)^version: ".*"$', f'version: "{version}"', manifest)
    if n != 1:
        sys.exit("could not find `version:` in umbrel-app.yml")
    if a.notes is not None:
        lines = [ln.rstrip().replace("\t", "    ") for ln in a.notes.strip().splitlines()]
        notes = "\n".join(f"  {ln}" if ln else "" for ln in lines)
        # replaces the old value, one line or a block: everything up to the next top-level key
        manifest, n = re.subn(r'(?ms)^releaseNotes:.*?(?=^\S)', f"releaseNotes: |-\n{notes}\n", manifest)
        if n != 1:
            sys.exit("could not find `releaseNotes:` in umbrel-app.yml")
    (APP / "umbrel-app.yml").write_text(manifest)
    print(f"umbrel-app.yml: version {version}")


if __name__ == "__main__":
    main()
