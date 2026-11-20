#!/usr/bin/env python3
"""Generate PNG icons (16x16, 48x48, 128x128) for the Chrome/Edge extension using stdlib only."""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    length = struct.pack(">I", len(data))
    crc = struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    return length + chunk_type + data + crc


def encode_rgba_png(width: int, height: int, pixels: bytes) -> bytes:
    """Encode raw RGBA bytes (height * width * 4) into a valid PNG."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    stride = width * 4
    raw_rows = bytearray()
    for y in range(height):
        raw_rows.append(0)  # Filter type 0 (None)
        start = y * stride
        raw_rows.extend(pixels[start : start + stride])
    idat = zlib.compress(bytes(raw_rows), level=9)
    return (
        signature
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def _alpha_blend(
    dst: tuple[int, int, int, int], src: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    sr, sg, sb, sa = src
    if sa <= 0:
        return dst
    if sa >= 255:
        return src
    dr, dg, db, da = dst
    af = sa / 255.0
    inv = 1.0 - af
    out_a = sa + da * inv
    if out_a <= 0:
        return (0, 0, 0, 0)
    r = int(round((sr * af + dr * (da / 255.0) * inv) / (out_a / 255.0)))
    g = int(round((sg * af + dg * (da / 255.0) * inv) / (out_a / 255.0)))
    b = int(round((sb * af + db * (da / 255.0) * inv) / (out_a / 255.0)))
    return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)), int(round(out_a)))


def _sd_rounded_box(px: float, py: float, cx: float, cy: float, hw: float, hh: float, r: float) -> float:
    dx = abs(px - cx) - (hw - r)
    dy = abs(py - cy) - (hh - r)
    outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
    inside = min(max(dx, dy), 0.0)
    return outside + inside - r


def _coverage_from_sd(sd: float, edge: float = 0.025) -> float:
    if sd <= -edge:
        return 1.0
    if sd >= edge:
        return 0.0
    return 0.5 - sd / (2.0 * edge)


def _sample_pixel(nx: float, ny: float) -> tuple[int, int, int, int]:
    """Sample normalized [0, 1] x [0, 1] icon coordinates."""
    # Rounded square badge
    bg_sd = _sd_rounded_box(nx, ny, 0.5, 0.5, 0.46, 0.46, 0.16)
    bg_cov = _coverage_from_sd(bg_sd, 0.02)
    if bg_cov <= 0.0:
        return (0, 0, 0, 0)

    # Subtle vertical gradient in dark indigo (#13111c -> #1e1830)
    t = max(0.0, min(1.0, ny))
    base = (
        int(19 + t * 13),
        int(17 + t * 8),
        int(28 + t * 22),
        int(round(255 * bg_cov)),
    )

    # Inner glowing violet border ring (#a78bfa)
    ring_inner = _sd_rounded_box(nx, ny, 0.5, 0.5, 0.41, 0.41, 0.12)
    ring_cov = max(0.0, bg_cov - _coverage_from_sd(ring_inner, 0.02))
    if ring_cov > 0.0:
        base = _alpha_blend(base, (167, 139, 250, int(round(235 * ring_cov))))

    # Subtle radial glow behind emblem
    dist_center = math.hypot(nx - 0.5, ny - 0.48)
    if dist_center < 0.36:
        glow = (1.0 - dist_center / 0.36) ** 2 * 0.28
        base = _alpha_blend(base, (167, 139, 250, int(round(255 * glow * bg_cov))))

    # Top emblem: stylized "RZ" / Re:Zero diamond + subtitle bars at bottom
    # Diamond crystal mark in upper half (centered at 0.5, 0.36)
    diamond_dist = abs(nx - 0.5) / 0.16 + abs(ny - 0.33) / 0.14
    if diamond_dist <= 1.1:
        d_cov = max(0.0, min(1.0, (1.0 - diamond_dist) * 8.0 + 0.5))
        if d_cov > 0.0:
            base = _alpha_blend(base, (196, 181, 253, int(round(255 * d_cov))))
            inner_diamond = abs(nx - 0.5) / 0.075 + abs(ny - 0.33) / 0.065
            if inner_diamond <= 1.1:
                id_cov = max(0.0, min(1.0, (1.0 - inner_diamond) * 8.0 + 0.5))
                base = _alpha_blend(base, (255, 255, 255, int(round(255 * id_cov))))

    # Subtitle bar 1 (full crisp white bar)
    bar1_sd = _sd_rounded_box(nx, ny, 0.5, 0.60, 0.26, 0.048, 0.028)
    bar1_cov = _coverage_from_sd(bar1_sd, 0.018)
    if bar1_cov > 0.0:
        base = _alpha_blend(base, (248, 246, 255, int(round(255 * bar1_cov))))

    # Subtitle bar 2 (shorter Brazilian green/gold accent split: green-gold-violet subtle nod -> bright emerald + gold-amber or crisp amethyst)
    bar2_left_sd = _sd_rounded_box(nx, ny, 0.39, 0.74, 0.13, 0.048, 0.028)
    bar2_left_cov = _coverage_from_sd(bar2_left_sd, 0.018)
    if bar2_left_cov > 0.0:
        base = _alpha_blend(base, (52, 211, 153, int(round(255 * bar2_left_cov))))  # #34d399 BR green

    bar2_right_sd = _sd_rounded_box(nx, ny, 0.64, 0.74, 0.09, 0.048, 0.028)
    bar2_right_cov = _coverage_from_sd(bar2_right_sd, 0.018)
    if bar2_right_cov > 0.0:
        base = _alpha_blend(base, (251, 191, 36, int(round(255 * bar2_right_cov))))  # #fbbf24 BR gold

    return base


def render_icon(size: int, ssaa: int = 4) -> bytes:
    """Render RGBA buffer with supersampling."""
    buf = bytearray(size * size * 4)
    inv_ssaa = 1.0 / (ssaa * ssaa)
    for y in range(size):
        for x in range(size):
            ar = ag = ab = aa = 0.0
            for sy in range(ssaa):
                ny = (y + (sy + 0.5) / ssaa) / size
                for sx in range(ssaa):
                    nx = (x + (sx + 0.5) / ssaa) / size
                    r, g, b, a = _sample_pixel(nx, ny)
                    af = a / 255.0
                    ar += r * af
                    ag += g * af
                    ab += b * af
                    aa += a
            out_a = int(round(aa * inv_ssaa))
            if out_a > 0:
                af_avg = (aa * inv_ssaa) / 255.0
                out_r = max(0, min(255, int(round((ar * inv_ssaa) / af_avg))))
                out_g = max(0, min(255, int(round((ag * inv_ssaa) / af_avg))))
                out_b = max(0, min(255, int(round((ab * inv_ssaa) / af_avg))))
            else:
                out_r = out_g = out_b = 0
            idx = (y * size + x) * 4
            buf[idx : idx + 4] = bytes((out_r, out_g, out_b, out_a))
    return encode_rgba_png(size, size, bytes(buf))


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    icons_dir = root / "extension" / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    for size in (16, 48, 128):
        out_path = icons_dir / f"icon-{size}.png"
        png_bytes = render_icon(size, ssaa=4)
        out_path.write_bytes(png_bytes)
        print(f"Created {out_path} ({size}x{size}, {len(png_bytes)} bytes)")


if __name__ == "__main__":
    main()
