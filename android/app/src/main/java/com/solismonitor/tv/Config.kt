package com.solismonitor.tv

/** Where the Solis dashboard (the `web` service) is reachable. */
data class Address(val host: String, val port: Int) {
    /** The dashboard's wall-display page. */
    val url: String get() = "http://$host:$port/?view=tv"
}

object Config {
    const val DEFAULT_PORT = 8088

    private val HOST = Regex("^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")

    /**
     * Accepts what people type: `192.168.1.5`, `umbrel.local`, `http://umbrel.local:8088/`.
     * A port typed after the host is used when the port field is blank. Returns null if invalid.
     */
    fun parse(hostInput: String, portInput: String): Address? {
        var host = hostInput.trim().removePrefix("http://").removePrefix("https://")
        host = host.substringBefore('/')
        var port = portInput.trim().ifEmpty { null }
        if (':' in host) {
            if (host.count { it == ':' } != 1) return null          // IPv6 literals are not supported
            val typed = host.substringAfter(':')
            host = host.substringBefore(':')
            if (port == null) port = typed.ifEmpty { null }
        }
        if (!HOST.matches(host)) return null
        val number = if (port == null) DEFAULT_PORT else port.toIntOrNull() ?: return null
        if (number !in 1..65535) return null
        return Address(host, number)
    }
}
