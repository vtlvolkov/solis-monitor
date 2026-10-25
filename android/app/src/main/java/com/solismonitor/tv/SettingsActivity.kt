package com.solismonitor.tv

import android.app.Activity
import android.os.Bundle
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.TextView

/** Host and port of the Solis dashboard. Opened on first start, or by holding OK / pressing Menu. */
class SettingsActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        val host = findViewById<EditText>(R.id.host)
        val port = findViewById<EditText>(R.id.port)
        val error = findViewById<TextView>(R.id.error)
        val save = findViewById<Button>(R.id.save)

        Prefs.load(this)?.let {
            host.setText(it.host)
            port.setText(it.port.toString())
        } ?: port.setText(Config.DEFAULT_PORT.toString())

        fun submit() {
            val address = Config.parse(host.text.toString(), port.text.toString())
            if (address == null) {
                error.text = getString(R.string.invalid_address)
                error.visibility = TextView.VISIBLE
                return
            }
            Prefs.save(this, address)
            finish()
        }
        save.setOnClickListener { submit() }
        port.setOnEditorActionListener { _, action, _ ->
            if (action == EditorInfo.IME_ACTION_DONE) { submit(); true } else false
        }
        findViewById<Button>(R.id.cancel).setOnClickListener { finish() }
    }
}
