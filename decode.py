#!/usr/bin/env python3
"""
Beko AC IR code decoder / analyzer.
Wire format: base64( FastLZ_compress( uint16_le_microseconds[] ) )

FastLZ block format:
  000LLLLL + (L+1) literal bytes       — literal block
  LLLBBBBB BBBBBBBB                    — copy (L+2) bytes from offset -(B+1)
  111BBBBB LLLLLLLL BBBBBBBB           — same but length = 7 + extra_byte + 2
"""
import base64
import io
import statistics
import struct
import sys
from collections import Counter


# ── FastLZ ────────────────────────────────────────────────────────────────────

def fastlz_decompress(data: bytes) -> bytes:
    inp = io.BytesIO(data)
    out = bytearray()
    while True:
        hdr = inp.read(1)
        if not hdr:
            break
        h = hdr[0]
        lb, db = h >> 5, h & 0x1F
        if lb == 0:
            n = db + 1
            blk = inp.read(n)
            assert len(blk) == n, f"truncated literal at pos {inp.tell()}"
            out.extend(blk)
        else:
            if lb == 7:
                extra = inp.read(1)
                assert extra, "truncated length extension"
                lb += extra[0]
            n = lb + 2
            dist = (db << 8 | inp.read(1)[0]) + 1
            blk = bytearray()
            while len(blk) < n:
                blk.extend(out[-dist:][:n - len(blk)])
            out.extend(blk)
    return bytes(out)


# ── wire → timings → bits ─────────────────────────────────────────────────────

def b64_to_timings(b64: str) -> list[int]:
    """base64 → FastLZ decompress → list of µs timings (mark, space, ...)."""
    raw = base64.b64decode(b64.strip())
    dec = fastlz_decompress(raw)
    return [struct.unpack_from("<H", dec, i)[0] for i in range(0, len(dec) - 1, 2)]


def timings_to_bits(timings: list[int], threshold: int = 900) -> str:
    spaces = [timings[i] for i in range(3, len(timings), 2)]
    return "".join("1" if s >= threshold else "0" for s in spaces)


def b64_to_bits(b64: str) -> str:
    return timings_to_bits(b64_to_timings(b64))


def b64_to_int(b64: str) -> int:
    bits = b64_to_bits(b64)
    return int(bits, 2) if bits else 0


# ── capture quality score ─────────────────────────────────────────────────────

def score_code(b64: str) -> dict:
    """Score capture quality. Higher = more reliable."""
    try:
        t = b64_to_timings(b64)
    except Exception as e:
        return {"score": -1, "error": str(e)}

    if len(t) < 6:
        return {"score": 0, "error": "too short"}

    marks  = [t[i] for i in range(2, len(t), 2)]
    spaces = [t[i] for i in range(3, len(t), 2)]

    mark_std   = statistics.stdev(marks) if len(marks) > 1 else 0
    short      = [s for s in spaces if s < 900]
    long_      = [s for s in spaces if s >= 900]
    separation = (statistics.mean(long_) - statistics.mean(short)) if short and long_ else 0
    outliers   = len([v for v in marks + spaces if v > 10000])
    score      = max(0.0, 100 - mark_std) + separation / 10 - outliers * 20

    return {
        "score":          round(score, 1),
        "n_timings":      len(t),
        "n_bits":         len(spaces),
        "leader_mark_us": t[0],
        "leader_space_us":t[1],
        "mark_mean_us":   round(statistics.mean(marks)) if marks else 0,
        "mark_std_us":    round(mark_std, 1),
        "short_space_us": round(statistics.mean(short)) if short else 0,
        "long_space_us":  round(statistics.mean(long_)) if long_ else 0,
        "n_outliers":     outliers,
    }


# ── Beko 28-bit frame parser ──────────────────────────────────────────────────

FAN_VALS = {1: 0, 2: 9, 3: 2, 4: 10, 5: 4}
FAN_REV  = {v: k for k, v in FAN_VALS.items()}

FRAME_TYPES = {0b000: "wake", 0b001: "cmd", 0b010: "swing"}
MODE_NAMES  = {
    0b000: "COOL",
    0b001: "DEHUM",
    0b010: "FAN",
    0b011: "AUTO",
    0b100: "HEAT",
}

KNOWN_FRAMES = {
    0x88C0051: "OFF",
    0x88C00A6: "display toggle",
    0x8810089: "turbo ON",
    0x88C0A6C: "sessiz ON",
    0x88C0A7D: "sessiz OFF",
}

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
SWING_REV = {v: k for k, v in SWING_POS.items()}


def parse_frame(val: int) -> dict:
    """Parse a 28-bit Beko frame into named fields."""
    bits    = f"{val:028b}"
    power   = int(bits[8:10],  2)
    ftype   = int(bits[10:13], 2)
    mode3   = int(bits[13:16], 2)
    temp_n  = int(bits[16:20], 2)
    fan_raw = int(bits[20:24], 2)
    chk     = int(bits[24:28], 2)

    result = {
        "hex":     f"0x{val:07X}",
        "bits":    bits,
        "power":   "ON" if power == 0b00 else "OFF",
        "type":    FRAME_TYPES.get(ftype, f"?{ftype:03b}"),
        "mode3":   mode3,
        "temp_n":  temp_n,
        "fan_raw": fan_raw,
        "chk":     chk,
        "known":   KNOWN_FRAMES.get(val, ""),
    }

    if ftype == 0b010:  # swing frame
        result["swing_pos"] = SWING_REV.get((temp_n, fan_raw), f"?(hi={temp_n} lo={fan_raw})")
    else:
        result["frame_temp"] = temp_n + 15
        result["fan_speed"]  = FAN_REV.get(fan_raw, f"?{fan_raw}")
        result["mode_name"]  = MODE_NAMES.get(mode3, f"?{mode3:03b}")

    return result


def decode_b64(b64: str) -> dict:
    return parse_frame(b64_to_int(b64))


# ── pretty printers ───────────────────────────────────────────────────────────

def print_decoded(label: str, b64: str) -> None:
    f = decode_b64(b64)
    known = f"  ← {f['known']}" if f['known'] else ""
    if f['type'] == 'swing':
        detail = f"swing={f.get('swing_pos', '?')}"
    else:
        detail = f"mode={f.get('mode_name','?'):5s} temp={f.get('frame_temp','?'):2}°C  fan={f.get('fan_speed','?')}"
    print(f"{label:20s}  {f['hex']}  power={f['power']:2s}  type={f['type']:5s}  {detail}{known}")


def analyze(codes: list[str], label: str = "") -> str | None:
    """Score all captures, print summary, return best base64."""
    print(f"\n{'='*60}")
    if label:
        print(f"Button: {label}")
    print(f"{'='*60}")

    results = []
    for i, code in enumerate(codes):
        s = score_code(code)
        results.append((i, code, s))
        status = "OK" if s["score"] >= 0 else f"ERR: {s.get('error')}"
        print(
            f"  [{i}] score={s.get('score','-'):>6}  "
            f"timings={s.get('n_timings','-'):>3}  "
            f"bits={s.get('n_bits','-'):>3}  "
            f"mark_std={s.get('mark_std_us','-'):>5}µs  "
            f"{status}"
        )

    valid = [(i, c, s) for i, c, s in results if s["score"] >= 0]
    if not valid:
        print("No valid codes!")
        return None

    counts  = Counter(s["n_timings"] for _, _, s in valid)
    modal_n = counts.most_common(1)[0][0]
    modal   = [(i, c, s) for i, c, s in valid if s["n_timings"] == modal_n]
    best_i, best_code, best_s = max(modal, key=lambda x: x[2]["score"])

    print(f"\n  Best: [{best_i}]  score={best_s['score']}  timings={best_s['n_timings']}")
    print(f"  Leader: {best_s['leader_mark_us']}µs mark + {best_s['leader_space_us']}µs space")
    print(f"  Bit mark: {best_s['mark_mean_us']}µs ±{best_s['mark_std_us']}µs")
    print(f"  0-bit space: {best_s['short_space_us']}µs  1-bit space: {best_s['long_space_us']}µs")

    bits = b64_to_bits(best_code)
    val  = int(bits, 2)
    print(f"  Bits ({len(bits)}): {bits}")
    print(f"  Hex: 0x{val:07X}")
    f = parse_frame(val)
    if f["known"]:
        print(f"  Known: {f['known']}")

    return best_code


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage:")
        print("  decode.py <base64> [<base64> ...]   — decode one or more captures")
        print("  decode.py --hex <hex> [<hex> ...]   — parse raw 28-bit hex values")
        sys.exit(1)

    if args[0] == "--hex":
        for h in args[1:]:
            f = parse_frame(int(h, 16))
            print(f"{f['hex']}  {f['bits']}  power={f['power']}  type={f['type']}  ", end="")
            if f["type"] == "swing":
                print(f"swing={f.get('swing_pos','?')}", end="")
            else:
                print(f"mode={f.get('mode_name','?')}  temp={f.get('frame_temp','?')}°C  fan={f.get('fan_speed','?')}", end="")
            if f["known"]:
                print(f"  ← {f['known']}", end="")
            print()
    else:
        for b64 in args:
            print_decoded(b64[:20] + "…", b64)
