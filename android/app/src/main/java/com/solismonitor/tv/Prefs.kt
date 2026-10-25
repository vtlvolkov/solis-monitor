package com.solismonitor.tv

import android.content.Context

object Prefs {
    private const val FILE = "solis_tv"

    fun load(ctx: Context): Address? {
        val p = ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)
        val host = p.getString("host", null) ?: return null
        return Config.parse(host, p.getInt("port", Config.DEFAULT_PORT).toString())
    }

    fun save(ctx: Context, a: Address) {
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .putString("host", a.host).putInt("port", a.port).apply()
    }
}
