#!/usr/bin/env python3
"""Read the customer port (P1) of a DLMS/COSEM smart meter over HDLC, unencrypted.

Usage:
  python3 p1_serial_reader.py                       # read the serial port
  python3 p1_serial_reader.py --file dump.bin       # replay a binary capture
Dependency: pip install pyserial
"""
import argparse, datetime, json, struct, sys
from decimal import Decimal

PORT = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_AQ819YU4-if00-port0"

DEFAULT_NAMES = {
    "0-0:96.1.0": "sn",
    "0-0:96.1.1": "id",
    "1-0:1.7.0":  "imp_W",
    "1-0:2.7.0":  "exp_W",
    "1-0:1.8.0":  "imp_Wh",
    "1-0:2.8.0":  "exp_Wh",
    "1-0:1.8.1":  "imp_t1_Wh",
    "1-0:1.8.2":  "imp_t2_Wh",
    "1-0:2.8.1":  "exp_t1_Wh",
    "1-0:2.8.2":  "exp_t2_Wh",
    "1-0:32.7.0": "L1_V",
    "1-0:52.7.0": "L2_V",
    "1-0:72.7.0": "L3_V",
    "1-0:31.7.0": "L1_A",
    "1-0:51.7.0": "L2_A",
    "1-0:71.7.0": "L3_A",
}

DEFAULT_GROUPED_NAMES = {
    "0-0:96.1.0": "sn",
    "0-0:96.1.1": "id",
    "1-0:1.7.0":  "imp_W",
    "1-0:2.7.0":  "exp_W",
    "1-0:1.8.0":  "imp_Wh",
    "1-0:2.8.0":  "exp_Wh",
    # Codes sharing a name are grouped in a list, in the order listed here
    "1-0:1.8.1":  "imp_t_Wh",
    "1-0:1.8.2":  "imp_t_Wh",
    "1-0:2.8.1":  "exp_t_Wh",
    "1-0:2.8.2":  "exp_t_Wh",
    "1-0:32.7.0": "L_V",
    "1-0:52.7.0": "L_V",
    "1-0:72.7.0": "L_V",
    "1-0:31.7.0": "L_A",
    "1-0:51.7.0": "L_A",
    "1-0:71.7.0": "L_A",
}

# Choices for --names (None: keep raw OBIS codes)
NAME_SETS = {
    "obis":     None,
    "friendly": DEFAULT_NAMES,
    "grouped":  DEFAULT_GROUPED_NAMES,
}

UNITS = {
    27: "W",
    28: "VA",
    29: "var",
    30: "Wh",
    31: "VAh",
    32: "varh",
    33: "A",
    35: "V",
    44: "Hz"
}


# ---------- HDLC ----------
def crc16_x25(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def hdlc_frames(buf):
    """Extract complete frames from buf (bytearray, consumed as frames are yielded)."""
    while True:
        i = buf.find(0x7E)
        if i < 0:
            buf.clear(); return
        del buf[:i]
        if len(buf) < 3:
            return
        if buf[1] & 0xF0 != 0xA0:          # not a type 3 header
            del buf[:1]; continue
        length = ((buf[1] & 0x07) << 8) | buf[2]
        if len(buf) < length + 2:
            return                           # incomplete frame, wait for more
        frame = bytes(buf[1:length + 1])
        if buf[length + 1] != 0x7E or crc16_x25(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
            del buf[:1]; continue            # out of sync or bad FCS
        del buf[:length + 1]                 # keep closing 7E (may open next frame)
        yield frame


def hdlc_info(frame):
    """Return (segmented?, information field) of an HDLC frame."""
    segmented = bool(frame[0] & 0x08)
    p = 2
    for _ in range(2):                       # destination then source address
        while not frame[p] & 1:
            p += 1
        p += 1
    p += 1 + 2                               # control + HCS
    return segmented, frame[p:-2]


# ---------- A-XDR ----------
def read_len(d, p):
    n = d[p]; p += 1
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(d[p:p + k], "big"); p += k
    return n, p


FIXED = {3: ">?", 5: ">i", 6: ">I", 15: ">b", 16: ">h", 17: ">B", 18: ">H",
         20: ">q", 21: ">Q", 22: ">B", 23: ">f", 24: ">d"}


def read_data(d, p):
    tag = d[p]; p += 1
    if tag == 0:
        return None, p
    if tag in (1, 2):                        # array / structure
        n, p = read_len(d, p)
        items = []
        for _ in range(n):
            v, p = read_data(d, p); items.append(v)
        return items, p
    if tag in (9, 10, 12):                   # octet-string / visible-string / utf8
        n, p = read_len(d, p)
        return bytes(d[p:p + n]), p + n
    if tag == 25:                            # date-time
        return bytes(d[p:p + 12]), p + 12
    fmt = FIXED[tag]
    size = struct.calcsize(fmt)
    return struct.unpack(fmt, d[p:p + size])[0], p + size


def dlms_datetime(b):
    y, mo, dd, _, h, mi, s = struct.unpack(">HBBBBBB", b[:8])
    return datetime.datetime(y, mo, dd, h, mi, s).isoformat()


def obis(b):
    return f"{b[0]}-{b[1]}:{b[2]}.{b[3]}.{b[4]}"


def decode_apdu(apdu):
    if apdu[:3] == b"\xe6\xe7\x00":
        apdu = apdu[3:]
    if apdu[0] == 0xDB:
        raise ValueError("encrypted APDU (0xDB): decryption key required")
    if apdu[0] != 0x0F:
        raise ValueError(f"unexpected APDU 0x{apdu[0]:02x}")
    p = 5                                    # tag + invoke-id (4 bytes)
    n = apdu[p]; p += 1
    out = {"time": dlms_datetime(apdu[p:p + n]) if n == 12 else None}
    p += n
    body, _ = read_data(apdu, p)
    for item in body:
        code = obis(item[0])
        value = item[1]
        if isinstance(value, bytes):
            out[code] = value.decode(errors="replace")
            continue
        unit = ""
        if len(item) > 2:
            scaler, u = item[2]
            unit = UNITS.get(u, str(u))
            # Fixed-point value: integer × 10^scaler, computed exactly
            dec = Decimal(value).scaleb(scaler)
            # int if the value is integral (W, Wh), else float (V, A)
            value = int(dec) if dec == dec.to_integral_value() else float(dec)
        out[code] = {"value": value, "unit": unit}
    return out


def rename_obis(data, names=DEFAULT_NAMES):
    """Return a copy of decode_apdu() output with OBIS codes replaced by names.

    Keys not in `names` (e.g. "time" or unknown codes) are kept unchanged.
    When several OBIS codes share the same name, their values are grouped in
    a list, ordered as the codes appear in `names` (None for a code missing
    from `data`).
    """
    codes_by_name = {}
    for code, name in names.items():
        codes_by_name.setdefault(name, []).append(code)

    out = {}
    for key, value in data.items():
        name = names.get(key, key)
        codes = codes_by_name.get(name, [key])
        if len(codes) == 1:
            out[name] = value
            continue
        entry = out.setdefault(name, {"value": [None] * len(codes), "unit": ""})
        i = codes.index(key)
        if isinstance(value, dict):
            entry["value"][i] = value["value"]
            entry["unit"] = entry["unit"] or value["unit"]
        else:
            entry["value"][i] = value
    return out


def telegrams(source):
    buf, parts = bytearray(), b""
    for chunk in source:
        buf.extend(chunk)
        for frame in hdlc_frames(buf):
            segmented, info = hdlc_info(frame)
            parts += info
            if not segmented:
                try:
                    yield decode_apdu(parts)
                except Exception as e:
                    print(f"# telegram skipped: {e}", file=sys.stderr)
                parts = b""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=PORT)
    ap.add_argument("--file")
    ap.add_argument("--names", choices=NAME_SETS, default="grouped",
                    help="output keys: raw OBIS codes, friendly names, or friendly "
                         "names with phases/tariffs grouped in lists (default: grouped)")
    args = ap.parse_args()
    names = NAME_SETS[args.names]
    if args.file:
        source = [open(args.file, "rb").read()]
    else:
        import serial
        ser = serial.Serial(args.port, 115200, bytesize=8, parity="N", stopbits=1, timeout=1)
        source = iter(lambda: ser.read(256), None)

    for t in telegrams(source):
        out = rename_obis(t, names) if names else t
        print(json.dumps(out, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
