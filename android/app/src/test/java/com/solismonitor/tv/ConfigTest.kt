package com.solismonitor.tv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ConfigTest {
    @Test fun hostAndPort() {
        assertEquals(Address("192.168.1.50", 8088), Config.parse("192.168.1.50", "8088"))
        assertEquals(Address("umbrel.local", 9000), Config.parse(" umbrel.local ", " 9000 "))
    }

    @Test fun blankPortUsesDefault() {
        assertEquals(Address("umbrel.local", 8088), Config.parse("umbrel.local", ""))
    }

    @Test fun pastedUrlIsAccepted() {
        assertEquals(Address("umbrel.local", 8088), Config.parse("http://umbrel.local/", ""))
        assertEquals(Address("10.0.0.2", 8099), Config.parse("http://10.0.0.2:8099/?view=tv", ""))
    }

    @Test fun portFieldWinsOverPortInHost() {
        assertEquals(Address("10.0.0.2", 7000), Config.parse("10.0.0.2:8099", "7000"))
        assertEquals(Address("10.0.0.2", 8099), Config.parse("10.0.0.2:8099", ""))
    }

    @Test fun rejectsBadInput() {
        assertNull(Config.parse("", "8088"))
        assertNull(Config.parse("   ", ""))
        assertNull(Config.parse("bad host", ""))
        assertNull(Config.parse("-host", ""))
        assertNull(Config.parse("fe80::1", ""))
        assertNull(Config.parse("host", "0"))
        assertNull(Config.parse("host", "65536"))
        assertNull(Config.parse("host", "80a"))
    }

    @Test fun urlOpensTvView() {
        assertEquals("http://umbrel.local:8088/?view=tv", Address("umbrel.local", 8088).url)
    }
}
