#!/usr/bin/env python3
"""Generate the default wallpapers (pure Python, no dependencies): soft gradients with a glow."""
import math
import os
import struct
import sys
import zlib

W, H = 1600, 900


def png(path, rows):
    raw = b"".join(b"\x00" + r for r in rows)
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)) +
                chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def make(path, top, bottom, glow, gx, gy, strength, radius):
    xs = [(x / W - gx) ** 2 for x in range(W)]
    rows = []
    for y in range(H):
        t = y / (H - 1)
        base = [top[i] + (bottom[i] - top[i]) * t for i in range(3)]
        dy = ((y / H - gy) * H / W) ** 2
        row = bytearray()
        for x in range(W):
            g = strength * math.exp(-(xs[x] + dy) / radius)
            row += bytes(min(255, int(base[i] * (1 - g) + glow[i] * g)) for i in range(3))
        rows.append(bytes(row))
    png(path, rows)


def main(out):
    os.makedirs(out, exist_ok=True)
    make(os.path.join(out, "lagoon.png"), (14, 18, 28), (10, 30, 40), (47, 183, 176), 0.12, 0.95, 0.55, 0.12)
    make(os.path.join(out, "dusk.png"), (16, 14, 30), (30, 20, 52), (122, 124, 240), 0.85, 0.15, 0.55, 0.14)
    make(os.path.join(out, "paper.png"), (226, 232, 241), (244, 246, 250), (140, 170, 230), 0.8, 0.9, 0.45, 0.16)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "wallpapers"))
