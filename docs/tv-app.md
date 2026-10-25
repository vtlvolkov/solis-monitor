# TV display and Android TV app

![TV view](img/tv.png)

`?view=tv` is a wall-display page on one screen with no scrolling: the power-flow diagram (big, top left) with the Battery card under it (charge bars, voltage, current, SOH and the backup-runtime estimate), and on the right the four gauges as a 2×2 block of bars (Grid, Backup load, Battery charge, Battery discharge), a tall Power flows chart and a strip with today's energy. Left/right on the remote (or arrow keys) change the chart range. It only reads data, so it works with `WEB_AUTH_SETTINGS_ONLY=true` and no password.

The Android TV app opens that page full screen (a WebView, plus a screen for the host and port).

**Install it:** download `solis-tv.apk` from the [latest release](https://github.com/vtlvolkov/solis-monitor/releases/latest/download/solis-tv.apk), then either

- copy it to the TV with a USB stick and open it with a file manager (allow "install unknown apps"), or
- install it over the network: enable Developer options and network debugging on the TV, then `adb connect <tv-ip>:5555` and `adb install -r solis-tv.apk`.

On first start the app asks for the host and port of the dashboard (for example `192.168.1.50` and `8088`). Later, hold OK (or press Menu) to change them. If the dashboard can't be reached, the app shows Retry / Settings and retries every 15 s, so it recovers when the TV boots before the network is up.

**Build it yourself** (needs only Docker; the first build downloads the Android SDK, a few minutes):

```bash
sh android/build.sh          # -> android/dist/solis-tv.apk
```

The signing key is created once in `android/.keystore/` and reused, so a newer build installs over the old one. Releases are signed with the key stored in the repository secret `ANDROID_KEYSTORE_B64` (see [Development](development.md#releases-and-the-installer)); an APK signed with a different key has to be uninstalled before it can replace another.
