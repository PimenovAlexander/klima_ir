import base64
import struct

# ── физические тайминги протокола ──────────────────────────────────────────
LEADER_MARK  = 2940
LEADER_SPACE = 9700
BIT_MARK     = 501
ZERO_SPACE   = 501
ONE_SPACE    = 1559

# ── кодировка скорости вентилятора (bits[20:24]) ───────────────────────────
FAN_VALS = {1: 0, 2: 9, 3: 2, 4: 10, 5: 4}

# ── устройства: name → {ir_topic, input_number} ────────────────────────────
DEVICES = {
    "kabinet": {
        "ir_topic":     "zigbee2mqtt/Black Box IR/set",
        "input_number": "input_number.beko_kabinet_temp",
    },
    "salon": {
        "ir_topic":     "zigbee2mqtt/Plugged IR/set",
        "input_number": "input_number.beko_salon_temp",
    },
}

# ── состояние каждого устройства ────────────────────────────────────────────
states = {
    name: {"mode": "off", "prev_mode": "off", "temp": 22, "fan": 1, "swing": "pos1", "turbo": False}
    for name in DEVICES
}


def _fastlz1_compress(data: bytes) -> bytes:
    """FastLZ Level 1 greedy LZ77 compression (8 KB window, max match 264).

    The UFO-R11 / TS1201 firmware has a small receive buffer.  Literal-only
    encoding expands 118-byte timing data to 122 compressed bytes (3 Zigbee
    chunks), which the device silently truncates.  Proper back-reference
    compression reduces the same data to 26-48 bytes (1 chunk), matching the
    size range of authentic learned codes (39-79 bytes).
    """
    n = len(data)
    out = bytearray()

    def _emit_literals(start: int, end: int) -> None:
        while start < end:
            run = min(32, end - start)
            out.append(run - 1)
            out.extend(data[start:start + run])
            start += run

    i = 0
    pending = 0

    while i < n:
        best_len = 0
        best_dist = 0

        if i >= 2 and i + 3 <= n:
            window = max(0, i - 8191)
            for j in range(window, i):
                ml = 0
                cap = min(n - i, 264)
                span = i - j
                while ml < cap and data[j + ml % span] == data[i + ml]:
                    ml += 1
                if ml >= 3 and ml > best_len:
                    best_len = ml
                    best_dist = i - j

        if best_len >= 3:
            _emit_literals(pending, i)
            pending = i + best_len
            d = best_dist - 1
            ln = best_len - 2
            if ln <= 6:
                out.append((ln << 5) | (d >> 8))
                out.append(d & 0xFF)
            else:
                out.append(0xE0 | (d >> 8))
                out.append((ln - 7) & 0xFF)
                out.append(d & 0xFF)
            i += best_len
        else:
            i += 1

    _emit_literals(pending, n)
    return bytes(out)


def _frame_to_base64(frame_28bit):
    bits = f"{frame_28bit:028b}"
    timings = [LEADER_MARK, LEADER_SPACE]
    for b in bits:
        timings.append(BIT_MARK)
        timings.append(ONE_SPACE if b == "1" else ZERO_SPACE)
    timings.append(BIT_MARK)
    raw = b""
    for t in timings:
        raw += struct.pack("<H", t)
    return base64.b64encode(_fastlz1_compress(raw)).decode()


MODE_BITS = {
    "cool":     (0, 0b00),  # (bits[13], bits[14:16])
    "heat":     (1, 0b00),
    "dehum":    (0, 0b01),
    "fan_only": (0, 0b10),
    "auto":     (0, 0b11),
}
# для dehum и fan_only кондей игнорирует температуру — шлём 16
MODE_FIXED_TEMP = {"dehum": 16, "fan_only": 16}


def beko_frame(mode, temp, fan, wake=False):
    mode_b, mb = MODE_BITS[mode]
    frame_temp = MODE_FIXED_TEMP.get(mode, temp)
    swg  = 0b000 if wake else 0b001
    n4   = frame_temp - 15
    fv   = FAN_VALS[fan]
    tlo  = (frame_temp - 7) % 16 if mode_b == 0 else (frame_temp - 3) % 16
    chk  = (fv + tlo + mb - (8 if wake else 0)) % 16
    return (0b10001000 << 20 | 0b00 << 18 | swg << 15 | mode_b << 14 |
            mb << 12 | n4 << 8 | fv << 4 | chk)


def swing_frame(pos):
    hi, lo = SWING_POS[pos]
    chk = (lo + 4 + hi) & 0xF
    return (0b10001000 << 20 | 0b010 << 15 | 0b11 << 12 |
            hi << 8 | lo << 4 | chk)


# ── предвычисленные фиксированные коды ─────────────────────────────────────
OFF_CODE     = _frame_to_base64(0x88C0051)
DISPLAY_CODE = _frame_to_base64(0x88C00A6)
TURBO_CODE   = _frame_to_base64(0x8810089)

SWING_POS = {
    "pos1": (0, 4),
    "pos2": (0, 5),
    "pos3": (0, 6),
    "pos4": (0, 7),
    "pos5": (0, 8),
    "pos6": (0, 9),
    "auto": (1, 4),
    "stop": (1, 5),
}


def _publish(device_name, ir):
    topic = DEVICES[device_name]["ir_topic"]
    mqtt.publish(topic=topic, payload=f'{{"ir_code_to_send":"{ir}"}}')


def _send_ir(device_name):
    s    = states[device_name]
    mode = s["mode"]
    temp = s["temp"]
    fan  = s["fan"]
    prev = s["prev_mode"]

    if mode == "off":
        _publish(device_name, OFF_CODE)
        log.info(f"Beko [{device_name}]: OFF")
    elif mode in MODE_BITS:
        wake = (prev == "off")
        _publish(device_name, _frame_to_base64(beko_frame(mode, temp, fan, wake=wake)))
        log.info(f"Beko [{device_name}]: mode={mode} temp={temp} fan={fan} wake={wake}")
    else:
        log.error(f"Beko [{device_name}]: unknown mode '{mode}'")
        return

    s["prev_mode"] = mode


def _set_temp(device_name, temp):
    states[device_name]["temp"] = temp
    input_number.set_value(entity_id=DEVICES[device_name]["input_number"], value=temp)


def _device_from_topic(topic):
    # topic format: beko/<device>/set/<cmd>
    parts = topic.split("/")
    if len(parts) >= 2 and parts[1] in DEVICES:
        return parts[1]
    return None


# ── публикуем availability для всех устройств ──────────────────────────────
mqtt.publish(topic="beko/available", payload="online", retain=True)
log.info("beko_ir.py loaded OK")


@mqtt_trigger("beko/+/set/mode")
def on_mode(topic, payload, **kwargs):
    dev = _device_from_topic(topic)
    if not dev:
        return
    states[dev]["mode"] = payload
    _send_ir(dev)


@mqtt_trigger("beko/+/set/temperature")
def on_temperature(topic, payload, **kwargs):
    dev = _device_from_topic(topic)
    if not dev:
        return
    _set_temp(dev, int(float(payload)))
    _send_ir(dev)


@mqtt_trigger("beko/+/set/temp_up")
def on_temp_up(topic, payload, **kwargs):
    dev = _device_from_topic(topic)
    if not dev:
        return
    _set_temp(dev, min(30, states[dev]["temp"] + 1))
    _send_ir(dev)


@mqtt_trigger("beko/+/set/temp_down")
def on_temp_down(topic, payload, **kwargs):
    dev = _device_from_topic(topic)
    if not dev:
        return
    _set_temp(dev, max(16, states[dev]["temp"] - 1))
    _send_ir(dev)


@mqtt_trigger("beko/+/set/fan_mode")
def on_fan(topic, payload, **kwargs):
    dev = _device_from_topic(topic)
    if not dev:
        return
    states[dev]["fan"] = int(payload)
    _send_ir(dev)


@mqtt_trigger("beko/+/set/swing")
def on_swing(topic, payload, **kwargs):
    dev = _device_from_topic(topic)
    if not dev:
        return
    if payload not in SWING_POS:
        log.error(f"Beko [{dev}] unknown swing: {payload}")
        return
    states[dev]["swing"] = payload
    _publish(dev, _frame_to_base64(swing_frame(payload)))
    log.info(f"Beko [{dev}]: swing={payload}")


@mqtt_trigger("beko/+/set/display")
def on_display(topic, payload, **kwargs):
    dev = _device_from_topic(topic)
    if not dev:
        return
    _publish(dev, DISPLAY_CODE)
    log.info(f"Beko [{dev}]: display toggled")


@mqtt_trigger("beko/+/set/turbo")
def on_turbo(topic, payload, **kwargs):
    dev = _device_from_topic(topic)
    if not dev:
        return
    if payload == "on":
        states[dev]["turbo"] = True
        _publish(dev, TURBO_CODE)
        log.info(f"Beko [{dev}]: turbo ON")
    else:
        states[dev]["turbo"] = False
        _send_ir(dev)
        log.info(f"Beko [{dev}]: turbo OFF")
