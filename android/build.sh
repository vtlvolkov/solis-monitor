#!/bin/sh
# Build the Android TV app in Docker and write dist/solis-tv.apk. Needs only Docker.
# The signing key is created once in .keystore/ and reused, so a new build installs over the old one.
# Releases: put the same keystore in the ANDROID_KEYSTORE_B64 secret so every release has one signature.
set -eu
cd "$(dirname "$0")"
mkdir -p .keystore dist
if [ ! -f .keystore/debug.keystore ]; then
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD/.keystore:/k" gradle:8.9-jdk17 \
    keytool -genkeypair -keystore /k/debug.keystore -storepass android -keypass android \
      -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 36500 -dname "CN=Solis TV,O=home"
fi
# APP_VERSION=1.2.3 (set by the release workflow) becomes the app's version; 1.2.3 -> version code 10203.
version="${APP_VERSION:-1.0}"
code=1
if [ -n "${APP_VERSION:-}" ]; then code=$(echo "$version" | awk -F'[.-]' '{printf "%d", $1 * 10000 + $2 * 100 + $3}'); fi
docker build --build-arg "APP_VERSION=$version" --build-arg "APP_VERSION_CODE=$code" -t solis-tv-apk .
id=$(docker create solis-tv-apk)
trap 'docker rm "$id" > /dev/null' EXIT
docker cp "$id:/src/app/build/outputs/apk/release/app-release.apk" dist/solis-tv.apk
echo "Built $PWD/dist/solis-tv.apk"
