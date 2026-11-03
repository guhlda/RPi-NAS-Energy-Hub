# Reading a smart meter's customer port (P1) with a USB FTDI dongle

This document explains how to read, on a Raspberry Pi (or any Linux system), the customer port of a smart electricity meter that sends **unencrypted DLMS/COSEM over HDLC**. Decoding is done by the `p1_serial_reader.py` script.

Reference setup:

| Item | Value |
|---|---|
| Dongle | FTDI FT232R, USB `0403:6001` |
| Port | `/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_AQ819YU4-if00-port0` |
| Serial link | 115200 baud, 8N1 |
| Protocol | HDLC → DLMS *data-notification* (`0x0F`), unencrypted |
| Rate | 1 telegram every 5 s, split into 3 HDLC frames |

---

## 1. Connecting and identifying the port

### 1.1 Wiring

1. Plug the dongle's cable (usually RJ12) into the meter's customer port.
2. Plug the dongle into a USB port on the Pi.

The P1 signal is inverted (negative logic). The inversion is normally handled by the dongle itself.

On some meters the customer port is **disabled by default**. If no bytes arrive, ask the grid operator to enable it.

### 1.2 Identifying the dongle

```bash
lsusb
# Bus 001 Device 003: ID 0403:6001 Future Technology Devices International, Ltd FT232 Serial (UART) IC

dmesg | grep -i -E "ftdi|ttyUSB"
# ftdi_sio ... FTDI USB Serial Device converter now attached to ttyUSB0
```

The kernel driver is `ftdi_sio`, which ships with Raspberry Pi OS. Nothing needs to be installed.

### 1.3 Using a stable path

`/dev/ttyUSB0` can change between boots, or when another serial adapter is plugged in. Use the symlink based on the FTDI serial number instead:

```bash
ls -l /dev/serial/by-id/
# usb-FTDI_FT232R_USB_UART_AQ819YU4-if00-port0 -> ../../ttyUSB0
```

Optionally, create a short name with a udev rule in `/etc/udev/rules.d/99-p1.rules`:

```
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", ATTRS{serial}=="AQ819YU4", SYMLINK+="p1"
```

```bash
sudo udevadm control --reload && sudo udevadm trigger   # → /dev/p1
```

### 1.4 Access rights

```bash
sudo usermod -aG dialout $USER     # then log out and back in
```

### 1.5 Checking reception

```bash
P=/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_AQ819YU4-if00-port0
stty -F $P 115200 cs8 -parenb -cstopb raw
timeout 15 cat $P | xxd | head -20          # or: | od -An -tx1 if xxd is missing
```

To identify the kind of stream you receive, compare with this table:

| What you see | Meaning |
|---|---|
| Text `/XXX5…` ending with `!CRC` | DSMR / IEC 62056-21 (text) |
| Binary with `7e a0`/`7e a8` … `e6 e7 00 0f` | **Unencrypted DLMS** (our case) |
| Binary with `… e6 e7 00 db` | **Encrypted** DLMS: the operator's key is required |
| Nothing | Check the speed (9600 7E1 on older meters) and that the port is enabled |

The serial format must be **8N1**. In 7E1 the 8th bit is lost, and you only see a few readable fragments among corrupted bytes.

### 1.6 Running the decoder

```bash
pip install pyserial            # or: sudo apt install python3-serial
python3 p1_serial_reader.py                     # 1 JSON object per telegram
python3 p1_serial_reader.py --file dump.bin     # replay a capture
python3 p1_serial_reader.py --names friendly    # choose the output keys (see below)
```

| Option | Default | Role |
|---|---|---|
| `--port PORT` | FTDI by-id path | Serial port to read |
| `--file FILE` | — | Replay a binary capture instead of reading the port |
| `--names {obis,friendly,grouped}` | `grouped` | Output keys: raw OBIS codes, friendly labels (`DEFAULT_NAMES`), or friendly labels with phases and tariffs grouped in lists (`DEFAULT_GROUPED_NAMES`). See §2.7. |

---

## 2. Protocol

### 2.1 Protocol stack

```
┌──────────────────────────────────────────────┐
│ OBIS        : what? (1-0:1.8.0 = energy)      │  IEC 62056-6-1
│ COSEM       : objects, value + scaler/unit    │  IEC 62056-6-2
│ DLMS / A-XDR: typed data-notification APDU    │  IEC 62056-5-3
│ LLC         : E6 E7 00                         │  IEC 62056-46
│ HDLC        : frames, segmentation, CRC        │  IEC 62056-46 / ISO 13239
│ UART        : 115200 8N1                       │
└──────────────────────────────────────────────┘
```

The meter **pushes** its data (unacknowledged UI frames). Nothing is ever sent to it: the link is listen-only.

### 2.2 HDLC frame

The first frame of the capture:

```
7E | A8 A4 | CF | 02 23 | 03 | 99 96 | E6 E7 00 0F … | 58 37 | 7E
 ①     ②     ③     ④     ⑤     ⑥          ⑦             ⑧     ①
```

| # | Field | Size | Details |
|---|---|---|---|
| ① | Flag | 1 | `0x7E`, at the start and end of the frame. Two consecutive frames give `7E 7E`. |
| ② | Format | 2 | Bits: `1010 S LLL LLLLLLLL`. `A` = type 3, **S** = another segment follows, L = 11-bit length excluding flags. `A8 A4` means segmented, 164 bytes. |
| ③ | Destination address | 1–4 | Variable length. The last byte has bit 0 set (`CF`). |
| ④ | Source address | 1–4 | Same rule (`02 23`). |
| ⑤ | Control | 1 | `03` = UI frame, `13` = UI frame with the P/F bit, used on the last segment. |
| ⑥ | HCS | 2 | Header CRC (②→⑤). |
| ⑦ | Information | n | Payload. Only the first segment starts with the LLC `E6 E7 00`. |
| ⑧ | FCS | 2 | CRC over the whole frame (②→⑦), little-endian. |

The CRC is **CRC-16/X.25**: reflected polynomial 0x1021 (0x8408), initial value 0xFFFF, final XOR 0xFFFF (RFC 1662).

There is **no byte stuffing**. A `0x7E` byte can therefore appear in the data. To delimit a frame reliably, rely on the **length field** and the FCS, not on splitting at `7E`.

### 2.3 Segmentation

The telegram (≈ 340 bytes) exceeds the maximum frame size, so it is split:

| Frame | Format | S | Content |
|---|---|---|---|
| 1 | `A8 A4` | 1 | `E6 E7 00` + start of the APDU |
| 2 | `A8 A4` | 1 | continuation |
| 3 | `A0 2E` | 0 | end |

To rebuild the APDU, concatenate the ⑦ fields of each frame until S = 0. A segment boundary can fall anywhere, including in the middle of an OBIS code.

### 2.4 *data-notification* APDU

```
0F | 00 00 27 31 | 0C 07EA 0A 03 06 07 2F 14 00 FF88 80 | 02 10 …
 ①       ②                        ③                      ④
```

| # | Field | Details |
|---|---|---|
| ① | APDU tag | `0F` = data-notification (unencrypted). `DB` = general-glo-ciphering (encrypted). |
| ② | Long-invoke-id | 32-bit counter incremented with each telegram. |
| ③ | Date-time | Length `0C` (12) followed by a COSEM date (`00` = absent). |
| ④ | Body | One A-XDR data item, here a structure of 16 elements. |

The 12-byte COSEM date breaks down as follows:

| Bytes | Field | Example |
|---|---|---|
| 0–1 | Year | `07EA` = 2026 |
| 2 | Month | `0A` = October |
| 3 | Day | `03` |
| 4 | Day of week (1 = Monday) | `06` = Saturday |
| 5–7 | h, min, s | 07:47:20 |
| 8 | Hundredths | `00` (`FF` = not specified) |
| 9–10 | UTC deviation (min, int16) | `FF88` = −120 |
| 11 | Clock status | `80` = daylight saving time active |

### 2.5 A-XDR encoding

Each data item is preceded by a type tag:

| Tag | Type | Size |
|---|---|---|
| `01` / `02` | array / structure | element count, then the elements |
| `09` | octet-string | length + bytes |
| `0A` | visible-string | length + bytes |
| `05` / `06` | int32 / **uint32** | 4 |
| `0F` / `11` | **int8** / uint8 | 1 |
| `10` / `12` | int16 / **uint16** | 2 |
| `16` | **enum** | 1 |
| `14` / `15` | int64 / uint64 | 8 |
| `17` / `18` | float32 / float64 | 4 / 8 |

All values are **big-endian**. A length above 127 is encoded on several bytes: `0x81 nn` or `0x82 nn nn`.

Each element of the body is a structure containing an OBIS code, a value and, where applicable, a scaler and a unit:

```
02 03                       structure (3)
   09 06 01 00 01 07 00 FF  octet-string(6) → OBIS 1-0:1.7.0.255
   06 00 00 00 A3           uint32 → 163
   02 02 0F 00 16 1B        structure(2) { int8 scaler = 0, enum unit = 27 (W) }
```

Elements without a unit, such as identifiers, are 2-element structures: `02 02 | OBIS | value`.

### 2.6 Fixed-point values: scaler and unit

The meter **sends only integers**. The physical value is computed as:

```
value = integer × 10^scaler
```

| Quantity | Raw type | Scaler | Example |
|---|---|---|---|
| Power | uint32 | 0 | 163 → 163 W |
| Energy | uint32 | 0 | 24399901 → 24399901 Wh |
| Voltage | uint16 | −1 | 2356 → 235.6 V |
| Current | uint16 | −2 | 40 → 0.40 A |

The scaler is sent **in every telegram** and depends on the meter model and configuration. It must not be hard-coded.

The script does the computation with `Decimal` (`scaleb`), which is exact. It outputs an `int` when the result is integral (W, Wh) and a `float` otherwise (V, A).

COSEM unit codes encountered, as defined in the *Register* class of the Blue Book:

| Code | Unit | Code | Unit |
|---|---|---|---|
| 27 | W | 31 | VAh |
| 28 | VA | 32 | varh |
| 29 | var | 33 | A |
| 30 | Wh | 35 | V |
| | | 44 | Hz |

### 2.7 OBIS codes

An OBIS code (*Object Identification System*) universally identifies a metering quantity. It takes 6 bytes and is written `A-B:C.D.E*F`.

| Field | Role | Example for `1-0:1.8.1.255` |
|---|---|---|
| A | Medium (0 abstract, 1 electricity, 7 gas, 8 water…) | 1 = electricity |
| B | Channel | 0 |
| C | Physical quantity | 1 = imported active power (+A) |
| D | Processing (7 = instantaneous, 8 = integral, i.e. energy) | 8 = cumulative energy |
| E | Tariff (0 = total) | 1 = tariff 1 |
| F | Billing period (255 = current value) | 255 |

Common values of C: 1 = +A (import), 2 = −A (export), 3/4 = reactive, 31/51/71 = current L1/L2/L3, 32/52/72 = voltage L1/L2/L3. A=0, C=96 denotes service information (identification numbers).

The 16 codes sent by this meter, with the labels used in `p1_serial_reader.py`. The *Friendly* column is `DEFAULT_NAMES` (`--names friendly`), the *Grouped* column is `DEFAULT_GROUPED_NAMES` (`--names grouped`, the default); the list index is given in brackets.

| OBIS | Friendly | Grouped | Quantity | Output unit |
|---|---|---|---|---|
| 0-0:96.1.0 | `sn` | `sn` | Meter serial number | — |
| 0-0:96.1.1 | `id` | `id` | Device identifier | — |
| 1-0:1.7.0 | `imp_W` | `imp_W` | Imported active power | W |
| 1-0:2.7.0 | `exp_W` | `exp_W` | Exported active power | W |
| 1-0:1.8.0 | `imp_Wh` | `imp_Wh` | Imported energy, total | Wh |
| 1-0:2.8.0 | `exp_Wh` | `exp_Wh` | Exported energy, total | Wh |
| 1-0:1.8.1 | `imp_t1_Wh` | `imp_t_Wh[0]` | Imported energy, tariff 1 | Wh |
| 1-0:1.8.2 | `imp_t2_Wh` | `imp_t_Wh[1]` | Imported energy, tariff 2 | Wh |
| 1-0:2.8.1 | `exp_t1_Wh` | `exp_t_Wh[0]` | Exported energy, tariff 1 | Wh |
| 1-0:2.8.2 | `exp_t2_Wh` | `exp_t_Wh[1]` | Exported energy, tariff 2 | Wh |
| 1-0:32.7.0 | `L1_V` | `L_V[0]` | Voltage L1 | V |
| 1-0:52.7.0 | `L2_V` | `L_V[1]` | Voltage L2 | V |
| 1-0:72.7.0 | `L3_V` | `L_V[2]` | Voltage L3 | V |
| 1-0:31.7.0 | `L1_A` | `L_A[0]` | Current L1 | A |
| 1-0:51.7.0 | `L2_A` | `L_A[1]` | Current L2 | A |
| 1-0:71.7.0 | `L3_A` | `L_A[2]` | Current L3 | A |

The meter only sends two tariffs (no `1.8.3`). The totals are consistent: `1.8.0 = 1.8.1 + 1.8.2` and `2.8.0 = 2.8.1 + 2.8.2`.

The labels are freely chosen names, stored in the `DEFAULT_NAMES` and `DEFAULT_GROUPED_NAMES` dictionaries. The `NAME_SETS` dictionary maps each `--names` choice to one of them (`obis` → no renaming). Renaming rules, applied by `rename_obis()`:

- A code missing from the dictionary is output in its raw OBIS form. `time` is never renamed.
- When **several codes share the same label**, their values are merged into one entry whose `value` is a list, ordered as the codes appear in the dictionary. The unit is kept. A code absent from the telegram leaves `null` in its slot. For example, `L_V` gives `{"value": [235.6, 236.1, 234.9], "unit": "V"}`.
- To group other quantities, give their codes the same label. To stop grouping, give them distinct labels.

### 2.8 Encrypted stream (for reference)

If the APDU starts with `DB` (*general-glo-ciphering*), it is structured as follows:

- `08` followed by the 8-byte *system title*, which often starts with the manufacturer code (`4C 47 5A` = "LGZ" for Landis+Gyr);
- the length;
- the security byte (`0x30` = encrypted and authenticated);
- the 4-byte frame counter;
- the AES-128-GCM ciphertext.

The IV is *system title* ‖ frame counter. Decryption requires the **GUEK** key, and often the authentication key as well, both provided by the grid operator. Once decrypted, the payload has the same structure as above. `p1_serial_reader.py` does not handle this case and reports the telegram as encrypted.

---

## 3. `p1_serial_reader.py` script: structure

| Function | Role |
|---|---|
| `crc16_x25()` | FCS/HCS computation |
| `hdlc_frames()` | Extracts frames from a byte stream (by length, with FCS check and resynchronisation) |
| `hdlc_info()` | Skips addresses, control and HCS, returns the S bit and the information field |
| `read_data()` | Recursive A-XDR decoder |
| `decode_apdu()` | LLC, data-notification, date, then scaler and unit conversion. Returns a dictionary keyed by **OBIS code** |
| `rename_obis()` | Replaces OBIS codes with labels from a name dictionary, grouping codes that share a label in lists (§2.7) |
| `telegrams()` | Reassembles segments, yields one `decode_apdu()` dictionary per telegram |
| `main()` | Parses the command line, applies the `--names` choice and prints the JSON |

Sample output with `--names obis`:

```json
{"time": "2026-10-03T07:47:20", "0-0:96.1.0": "85742077", "0-0:96.1.1": "1062326",
 "1-0:1.7.0": {"value": 163, "unit": "W"}, "1-0:32.7.0": {"value": 235.6, "unit": "V"}, "…": "…"}
```

With `--names friendly`:

```json
{"time": "2026-10-03T07:47:20", "sn": "85742077", "id": "1062326",
 "imp_W": {"value": 163, "unit": "W"}, "imp_Wh": {"value": 24399901, "unit": "Wh"},
 "L1_V": {"value": 235.6, "unit": "V"}, "L1_A": {"value": 0.4, "unit": "A"}, "…": "…"}
```

With `--names grouped` (default):

```json
{"time": "2026-10-03T07:47:20", "sn": "85742077", "id": "1062326",
 "imp_W": {"value": 163, "unit": "W"}, "imp_Wh": {"value": 24399901, "unit": "Wh"},
 "imp_t_Wh": {"value": [16123456, 8276445], "unit": "Wh"},
 "L_V": {"value": [235.6, 236.1, 234.9], "unit": "V"},
 "L_A": {"value": [0.4, 1.12, 0.05], "unit": "A"}, "…": "…"}
```

The script actually prints each telegram indented on several lines (`indent=2`). The samples above are compacted.

---

## 4. References

**Standards**

- **IEC 62056-46**: *Data link layer using HDLC protocol*. HDLC frame, addresses, segmentation, LLC.
- **ISO/IEC 13239**: HDLC procedures (frame format type 3).
- **RFC 1662**, appendix C: FCS-16 algorithm (CRC-16/X.25). https://www.rfc-editor.org/rfc/rfc1662
- **IEC 62056-5-3**: *DLMS/COSEM application layer*. APDUs, data-notification, glo-ciphering.
- **IEC 62056-6-1** (formerly IEC 62056-61): *Object Identification System (OBIS)*.
- **IEC 62056-6-2** (formerly IEC 62056-62): *COSEM interface classes*. Register class, scaler_unit, unit codes.
- **IEC 62056-21**: text protocol (mode C), used by older meters and by DSMR.

**DLMS User Association documentation** (https://www.dlms.com)

- *Blue Book*: COSEM interface classes and OBIS, with the list of OBIS codes and units.
- *Green Book*: architecture and protocols (HDLC, A-XDR, security).

**Manufacturer and grid operator documentation**

- The meter's customer interface description (e.g. Landis+Gyr E450/E360 "Customer Interface"), which gives the exact list of pushed OBIS codes, the push interval and the encryption settings. Available from the manufacturer or the grid operator.
- FTDI, *FT232R USB UART IC Datasheet*. https://ftdichip.com

**Software**

- *smartmeter-datacollector* (Supercomputing Systems AG, funded by EKZ): data collection from Swiss L+G E450/E360 and Iskraemeco AM550 meters, with decryption, MQTT and systemd. https://github.com/scs/smartmeter-datacollector
- Gurux DLMS (Python, C, C++…): full DLMS/COSEM stack. https://www.gurux.fi
- `dsmr_parser`: Python decoder for the DSMR text format (Dutch/Belgian P1). https://github.com/ndokter/dsmr_parser
