# TV display and Android TV app

![TV view](img/tv.png)

`?view=tv` is a wall-display page on one screen with no scrolling: the power-flow diagram (big, top left) with the Battery card under it (charge bars, voltage, current, SOH and the backup-runtime estimate), and on the right the four gauges as a 2×2 block of bars (Grid, Backup load, Battery charge, Battery discharge), a tall Power flows chart and a strip with today's energy. Left/right on the remote (or arrow keys) change the chart range. It only reads data, so it works with `WEB_AUTH_SETTINGS_ONLY=true` and no password.

`android/` is a small app that opens this page full screen (a WebView, plus a screen for the host and port). It needs only Docker to build:

```bash
sh android/build.sh          # -> android/dist/solis-tv.apk (first build downloads the Android SDK, a few minutes)
adb connect <tv-ip>:5555     # enable Developer options -> USB/Network debugging on the TV first
adb install -r android/dist/solis-tv.apk
```

Or copy the APK to the TV with a USB stick and open it with a file manager (allow "install unknown apps"). On first start the app asks for the host and port of the dashboard (for example `192.168.1.50` and `8088`). Later, hold OK (or press Menu) to change them. If the dashboard can't be reached, the app shows Retry / Settings and retries every 15 s, so it recovers when the TV boots before the network is up.

The app talks plain HTTP (the dashboard has no TLS), so only use it on your own network. It keeps the screen on and reloads the page every 6 hours. The signing key is created in `android/.keystore/` (not committed): keep it, or the next build won't install over the old one.
