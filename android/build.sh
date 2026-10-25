#!/bin/sh
# Build the Android TV app in Docker and write dist/solis-tv.apk. Needs only Docker.
# The signing key is created once in .keystore/ and reused, so a new build installs over the old one.
set -eu
cd "$(dirname "$0")"
mkdir -p .keystore dist
if [ ! -f .keystore/debug.keystore ]; then
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD/.keystore:/k" gradle:8.9-jdk17 \
    keytool -genkeypair -keystore /k/debug.keystore -storepass android -keypass android \
      -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 36500 -dname "CN=Solis TV,O=home"
fi
docker build -t solis-tv-apk .
id=$(docker create solis-tv-apk)
trap 'docker rm "$id" > /dev/null' EXIT
docker cp "$id:/src/app/build/outputs/apk/release/app-release.apk" dist/solis-tv.apk
echo "Built $PWD/dist/solis-tv.apk"
