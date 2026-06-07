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
            out.append(run - 1)          # 000LLLLL header
            out.extend(data[start:start + run])
            start += run

    i = 0
    pending = 0                          # start of unemitted literal run

    while i < n:
        best_len = 0
        best_dist = 0

        if i >= 2 and i + 3 <= n:
            window = max(0, i - 8191)
            for j in range(window, i):
                ml = 0
                cap = min(n - i, 264)
                span = i - j             # distance; match wraps if span < cap
                while ml < cap and data[j + ml % span] == data[i + ml]:
                    ml += 1
                if ml >= 3 and ml > best_len:
                    best_len = ml
                    best_dist = i - j

        if best_len >= 3:
            _emit_literals(pending, i)
            pending = i + best_len
            d = best_dist - 1           # stored as dist-1
            ln = best_len - 2           # stored as len-2
            if ln <= 6:                 # 2-byte form: LLLDDDDD DDDDDDDD
                out.append((ln << 5) | (d >> 8))
                out.append(d & 0xFF)
            else:                       # 3-byte form: 111DDDDD LLLLLLLL DDDDDDDD
                out.append(0xE0 | (d >> 8))
                out.append((ln - 7) & 0xFF)
                out.append(d & 0xFF)
            i += best_len
        else:
            i += 1

    _emit_literals(pending, n)
    return bytes(out)


def _frame_to_base64(frame_28bit):
    """28-bit integer → Tuya base64 (FastLZ with back-references)."""
    bits = f"{frame_28bit:028b}"
    timings = [LEADER_MARK, LEADER_SPACE]
    for b in bits:
        timings.append(BIT_MARK)
        timings.append(ONE_SPACE if b == "1" else ZERO_SPACE)
    timings.append(BIT_MARK)  # trailing mark
    raw = b""
    for t in timings:
        raw += struct.pack("<H", t)
    return base64.b64encode(_fastlz1_compress(raw)).decode()


def beko_frame(mode, temp, fan, power="on", wake=False):
    """
    Собирает 28-битный фрейм протокола Beko 31225/30925.

    bits[0:8]   = 0x88  — фиксированный заголовок
    bits[8:10]  = 00=ON / 11=OFF
    bits[10:13] = 000=включение с параметрами / 001=команда (кондей уже включён)
    bits[13]    = 0=COOL / 1=HEAT
    bits[14:16] = 00
    bits[16:20] = temp - 15
    bits[20:24] = FAN_VALS[fan]
    bits[24:28] = (FAN_VALS[fan] + temp_lo) % 16  — контрольная сумма
    """
    pwr    = 0b00 if power == "on" else 0b11
    mode_b = 0 if mode == "cool" else 1
    swg    = 0b000 if wake else 0b001
    n4     = temp - 15
    fv     = FAN_VALS[fan]
    tlo    = (temp - 7) % 16 if mode == "cool" else (temp - 3) % 16
    chk    = (fv + tlo - (8 if wake else 0)) % 16
    return (0b10001000 << 20 | pwr << 18 | swg << 15 | mode_b << 14 |
            n4 << 8 | fv << 4 | chk)


def beko_to_base64(mode, temp, fan, power="on"):
    return _frame_to_base64(beko_frame(mode, temp, fan, power))


# ── ON: простое включение, bits[10:13]=000 — кондей должен получить его первым
ON_FRAME    = 0x880069F
ON_CODE     = _frame_to_base64(ON_FRAME)

# ── OFF: фиксированный фрейм 0x88C0051, верифицирован на 9 захватах ────────
OFF_FRAME     = 0x88C0051
OFF_CODE      = _frame_to_base64(OFF_FRAME)

# ── DISPLAY: toggle индикации, верифицирован на 6 захватах ──────────────────
DISPLAY_FRAME = 0x88C00A6
DISPLAY_CODE  = _frame_to_base64(DISPLAY_FRAME)

# ── TURBO: ON = спецфрейм 0x8810089, OFF = возврат к текущему state ─────────
TURBO_FRAME   = 0x8810089
TURBO_CODE    = _frame_to_base64(TURBO_FRAME)

# ── swing: bits[10:13]=010, bits[14:16]=11
# frame = 0x88 header | 010 | mode=0 | 11 | hi<<8 | lo<<4 | chk
# chk = lo + 4 + hi  (verified on all 7 positions)
SWING_POS = {
    "auto": (1, 4),
    "pos1": (0, 2),
    "pos2": (0, 3),   # works 
    "pos3": (0, 4),   # works
    "pos4": (0, 5),   # works
    "pos5": (0, 6),   # works
    "pos6": (0, 7)    # works
}


def swing_frame(pos):
    hi, lo = SWING_POS[pos]
    chk = (lo + 4 + hi) & 0xF
    return (0b10001000 << 20 | 0b010 << 15 | 0b11 << 12 |
            hi << 8 | lo << 4 | chk)

# ── текущее состояние ───────────────────────────────────────────────────────
state = {
    "mode":      "off",
    "prev_mode": "off",
    "temp":      22,
    "fan":       1,
    "swing":     "pos1",
    "turbo":     False,
}

log.info("beko_ir.py loaded OK")


def _publish(ir):
    mqtt.publish(
        topic="zigbee2mqtt/Black Box IR/set",
        payload=f'{{"ir_code_to_send":"{ir}"}}'
    )


def _sync_state():
    """Публикуем реальное состояние → HA обновляет UI.
    Потом сбрасываем в None → frontend перестаёт фильтровать повторные клики."""
    s = state
    mqtt.publish(topic="beko/state/mode",        payload=s["mode"])
    mqtt.publish(topic="beko/state/temperature",  payload=str(s["temp"]))
    mqtt.publish(topic="beko/state/fan_mode",     payload=str(s["fan"]))
    mqtt.publish(topic="beko/state/swing",        payload=s["swing"])
    task.sleep(0.5)
    mqtt.publish(topic="beko/state/mode",         payload="")
    mqtt.publish(topic="beko/state/temperature",  payload="")
    mqtt.publish(topic="beko/state/fan_mode",     payload="")
    mqtt.publish(topic="beko/state/swing",        payload="")


def send_ir():
    mode = state["mode"]
    temp = state["temp"]
    fan  = state["fan"]
    prev = state["prev_mode"]

    if mode == "off":
        _publish(OFF_CODE)
        log.info("Beko IR: OFF")
    elif mode in ("cool", "heat"):
        wake = (prev == "off")
        _publish(_frame_to_base64(beko_frame(mode, temp, fan, wake=wake)))
        log.info(f"Beko IR: mode={mode} temp={temp} fan={fan} wake={wake}")
    else:
        return

    state["prev_mode"] = mode
    _sync_state()


@mqtt_trigger("beko/set/mode")
def on_mode(topic, payload, **kwargs):
    state["mode"] = payload
    send_ir()


@mqtt_trigger("beko/set/temperature")
def on_temperature(topic, payload, **kwargs):
    state["temp"] = int(float(payload))
    send_ir()


@mqtt_trigger("beko/set/fan_mode")
def on_fan(topic, payload, **kwargs):
    state["fan"] = int(payload)
    send_ir()


@mqtt_trigger("beko/set/swing")
def on_swing(topic, payload, **kwargs):
    if payload not in SWING_POS:
        log.error(f"Beko unknown swing: {payload}")
        return
    state["swing"] = payload
    ir = _frame_to_base64(swing_frame(payload))
    mqtt.publish(
        topic="zigbee2mqtt/Black Box IR/set",
        payload=f'{{"ir_code_to_send":"{ir}"}}'
    )
    log.info(f"Beko swing: {payload}")


@mqtt_trigger("beko/set/display")
def on_display(topic, payload, **kwargs):
    _publish(DISPLAY_CODE)
    log.info("Beko display toggled")


@mqtt_trigger("beko/set/turbo")
def on_turbo(topic, payload, **kwargs):
    if payload == "on":
        state["turbo"] = True
        _publish(TURBO_CODE)
        log.info("Beko turbo ON")
    else:
        state["turbo"] = False
        send_ir()
        log.info("Beko turbo OFF")
