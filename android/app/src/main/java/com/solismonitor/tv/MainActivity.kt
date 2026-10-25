package com.solismonitor.tv

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.TextView
import android.widget.Toast

/** Full-screen WebView on the dashboard's `?view=tv` page. */
class MainActivity : Activity() {
    private lateinit var web: WebView
    private lateinit var errorPanel: View
    private lateinit var errorText: TextView
    private val handler = Handler(Looper.getMainLooper())
    private var loaded: String? = null
    private var failed = false
    private var askedForAddress = false
    private var hinted = false

    private val openSettings = Runnable { startActivity(Intent(this, SettingsActivity::class.java)) }
    private val retry = Runnable { load(force = true) }
    private val refresh = object : Runnable {            // a wall display runs for days: start over now and then
        override fun run() { load(force = true); handler.postDelayed(this, REFRESH_MS) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_main)
        web = findViewById(R.id.web)
        errorPanel = findViewById(R.id.error_panel)
        errorText = findViewById(R.id.error_text)
        findViewById<Button>(R.id.retry).setOnClickListener { load(force = true) }
        findViewById<Button>(R.id.open_settings).setOnClickListener { openSettings.run() }

        web.setBackgroundColor(Color.parseColor("#141517"))
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            cacheMode = WebSettings.LOAD_DEFAULT
            mediaPlaybackRequiresUserGesture = true
        }
        web.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) { failed = false }

            override fun onPageFinished(view: WebView, url: String) {
                if (!failed) {
                    errorPanel.visibility = View.GONE
                    handler.removeCallbacks(retry)
                    if (!hinted) {
                        hinted = true
                        Toast.makeText(this@MainActivity, R.string.hint_settings, Toast.LENGTH_LONG).show()
                    }
                }
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) showError(getString(R.string.cannot_reach, error.description))
            }

            override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
                if (request.isForMainFrame) showError(getString(R.string.http_error, response.statusCode))
            }

            // The renderer is killed on low-memory boxes; start a fresh WebView instead of crashing.
            override fun onRenderProcessGone(view: WebView, detail: android.webkit.RenderProcessGoneDetail): Boolean {
                handler.post { recreate() }
                return true
            }
        }
    }

    override fun onResume() {
        super.onResume()
        val address = Prefs.load(this)
        if (address == null) {
            if (askedForAddress) finish() else { askedForAddress = true; openSettings.run() }
            return
        }
        load(force = address.url != loaded)
        handler.removeCallbacks(refresh)
        handler.postDelayed(refresh, REFRESH_MS)
    }

    override fun onPause() {
        handler.removeCallbacks(refresh)
        handler.removeCallbacks(retry)
        handler.removeCallbacks(openSettings)
        super.onPause()
    }

    override fun onDestroy() {
        web.destroy()
        super.onDestroy()
    }

    private fun load(force: Boolean) {
        val address = Prefs.load(this) ?: return
        if (!force && loaded == address.url) return
        loaded = address.url
        failed = false
        web.loadUrl(address.url)
    }

    private fun showError(message: String) {
        failed = true
        errorText.text = message
        errorPanel.visibility = View.VISIBLE
        findViewById<Button>(R.id.retry).requestFocus()
        handler.removeCallbacks(retry)
        handler.postDelayed(retry, RETRY_MS)       // the box often boots before the network is up
    }

    /** Menu, or holding OK for 1.5 s, opens the settings (a TV remote has no other spare key). */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        when (event.keyCode) {
            KeyEvent.KEYCODE_MENU -> {
                if (event.action == KeyEvent.ACTION_UP) openSettings.run()
                return true
            }
            KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER -> {
                if (errorPanel.visibility != View.VISIBLE) {
                    if (event.action == KeyEvent.ACTION_DOWN && event.repeatCount == 0) {
                        handler.postDelayed(openSettings, HOLD_MS)
                    } else if (event.action == KeyEvent.ACTION_UP) {
                        handler.removeCallbacks(openSettings)
                    }
                }
            }
        }
        return super.dispatchKeyEvent(event)
    }

    private companion object {
        const val HOLD_MS = 1500L
        const val RETRY_MS = 15_000L
        const val REFRESH_MS = 6 * 60 * 60 * 1000L
    }
}
