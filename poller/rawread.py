"""Raw register reads for the dashboard's Debug tab.

Read-only on purpose: only function 3 (holding) and 4 (input) exist here. Writing a register by hand is not
offered anywhere (see control.py for the checked write path). The frame text is the Modbus PDU (function code and
data, without the TCP header or unit id), so what is shown is what the protocol guide describes.
"""

FUNCTIONS = {3: "Read holding registers", 4: "Read input registers"}
MAX_COUNT = 50        # Solis protocol limit per request, same as the poll blocks
EXCEPTIONS = {1: "Illegal function", 2: "Illegal data address", 3: "Illegal data value", 4: "Server device failure",
              5: "Acknowledge", 6: "Server device busy", 8: "Memory parity error", 10: "Gateway path unavailable",
              11: "Gateway target device failed to respond"}


def _int(value, name, lo, hi) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{name} must be a whole number from {lo} to {hi}")
    return value


def check(function, address, count) -> tuple[int, int, int]:
    """Validated (function, address, count); ValueError with a message fit for the dashboard otherwise."""
    if isinstance(function, bool) or function not in FUNCTIONS:
        raise ValueError("The function must be 3 (holding registers) or 4 (input registers)")
    address = _int(address, "The address", 0, 65535)
    count = _int(count, "The count", 1, MAX_COUNT)
    if address + count > 65536:
        raise ValueError("The range goes past register 65535")
    return function, address, count


def _hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def request_pdu(function: int, address: int, count: int) -> str:
    return _hex(bytes([function]) + address.to_bytes(2, "big") + count.to_bytes(2, "big"))


def response_pdu(function: int, registers: list[int]) -> str:
    return _hex(bytes([function, len(registers) * 2]) + b"".join(r.to_bytes(2, "big") for r in registers))


def exception_pdu(function: int, code: int) -> str:
    return _hex(bytes([function | 0x80, code]))
