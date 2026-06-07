#!/usr/bin/env python3
"""
UFO-R11 / Moes TS1201 / Tuya ZS06 IR code decoder.

Format: base64( fastlz_compress( u16le_microseconds ) )

FastLZ block format:
  000LLLLL + (L+1) literal bytes          — literal block
  LLLBBBBB BBBBBBBB                        — copy (L+2) bytes from offset -(B+1)
  111BBBBB LLLLLLLL BBBBBBBB               — same but length = 7 + extra_byte + 2
"""
import base64
import io
import statistics
import struct
import sys
from collections import Counter


def decompress_fastlz(data: bytes) -> bytes:
    inp = io.BytesIO(data)
    out = bytearray()
    while True:
        hdr = inp.read(1)
        if not hdr:
            break
        h = hdr[0]
        lb = h >> 5
        db = h & 0x1F
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


def decode_ir_code(b64: str) -> list[int]:
    """Return list of mark/space durations in microseconds (mark first)."""
    raw = base64.b64decode(b64)
    dec = decompress_fastlz(raw)
    return [struct.unpack_from("<H", dec, i)[0] for i in range(0, len(dec) - 1, 2)]


def score_code(b64: str) -> dict:
    """Score capture quality. Higher = more reliable."""
    try:
        t = decode_ir_code(b64)
    except Exception as e:
        return {"score": -1, "error": str(e)}

    if len(t) < 6:
        return {"score": 0, "error": "too short"}

    marks = [t[i] for i in range(2, len(t), 2)]
    spaces = [t[i] for i in range(3, len(t), 2)]

    mark_std = statistics.stdev(marks) if len(marks) > 1 else 0
    short = [s for s in spaces if s < 900]
    long_ = [s for s in spaces if s >= 900]
    separation = (statistics.mean(long_) - statistics.mean(short)) if short and long_ else 0
    outliers = len([v for v in marks + spaces if v > 10000])

    score = max(0.0, 100 - mark_std) + separation / 10 - outliers * 20

    return {
        "score": round(score, 1),
        "n_timings": len(t),
        "n_bits": len(spaces),
        "leader_mark_us": t[0],
        "leader_space_us": t[1],
        "mark_mean_us": round(statistics.mean(marks)) if marks else 0,
        "mark_std_us": round(mark_std, 1),
        "short_space_us": round(statistics.mean(short)) if short else 0,
        "long_space_us": round(statistics.mean(long_)) if long_ else 0,
        "n_outliers": outliers,
    }


def spaces_to_bits(spaces: list[int], threshold: int = 900) -> str:
    return "".join("1" if s >= threshold else "0" for s in spaces)


def analyze(codes: list[str], label: str = ""):
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

    counts = Counter(s["n_timings"] for _, _, s in valid)
    modal_n = counts.most_common(1)[0][0]
    modal = [(i, c, s) for i, c, s in valid if s["n_timings"] == modal_n]

    best_i, best_code, best_s = max(modal, key=lambda x: x[2]["score"])
    print(f"\n  Best: [{best_i}]  score={best_s['score']}  timings={best_s['n_timings']}")
    print(f"  Leader: {best_s['leader_mark_us']}µs mark + {best_s['leader_space_us']}µs space")
    print(f"  Bit mark: {best_s['mark_mean_us']}µs ±{best_s['mark_std_us']}µs")
    print(f"  0-bit space: {best_s['short_space_us']}µs  1-bit space: {best_s['long_space_us']}µs")

    timings = decode_ir_code(best_code)
    spaces = [timings[i] for i in range(3, len(timings), 2)]
    bits = spaces_to_bits(spaces)
    print(f"  Bits ({len(bits)}): {bits}")
    print(f"  Hex: {int(bits, 2):0{(len(bits)+3)//4}X}" if bits else "")

    return best_code


OFF_CODES = [
    "Cw0L4yXmAR0G5gEQAuABA0AP4AMB4AMb4A8LQDNAA0AfwAfAC0APCx0G5gEdBuYBHQbmAQ==",
    "CUAL9CXwARQG8AHgAwFADwEnAoADQAFAD0AD4AMT4BcBwC9AB+ADAQMUBvAB",
    "CXUL8CXoARgG6AFAAQEVAoADQA9AC+ALAUAX4AsDQDNAA0AbwAfAC0APCxgG6AEYBugBGAboAQ==",
    "CzALAibFATYGxQEZAkADQAEBxQHgAQ8FxQEZAhkCQA8HNgYZAhkCxQHgAQNAAeADD8ALCRkCxQHFATYGxQFACcAHC1gCxQEZAsUBNgbFAQ==",
    "CXwL5CX1ARcG9QHgAwHgBw/gGwFAM0ADQAHAB0ABQAvgBAMCBvUB",
    "DUsLBCbGASYGIwIjAsYBwAMDJgbGAcALQAcJJgYjAiYGxgEjAuAHA0AB4AcTgA/AL8AHCyMCxgEjAsYBJgbGAQ==",
    "C2EL2CXJATIGyQEcAuABA+AHDwBq4AIb4AEnQAHgAQ9AMwUyBhwCHAJABwHJAUAHQANAC+AEAwIGyQE=",
    "B3cL/SXXASkGIAMAAuACA6APQAEAKUAXQA8gA0ABACkgD0ABQAfgFAMgLwApICsAKSAHAtcBKSAJCdcBKQLXASkG1wE=",
    "CZoL2CXvARgG7wHgAwHADwEkAuAFA+ADAUAb4AMBQDNAA0ABwAdAAUAL4AQDAgbvAQ==",
]

if __name__ == "__main__":
    codes = sys.argv[1:] if len(sys.argv) > 1 else OFF_CODES
    analyze(codes, label="OFF (BEKO AC)")
