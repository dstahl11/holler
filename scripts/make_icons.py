#!/usr/bin/env python3
"""Generate PWA icons (megaphone on dark purple) -> static/icons/."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "static" / "icons"


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size), "#1a1023")
    d = ImageDraw.Draw(img)
    s = size / 512  # design at 512, scale down

    # Megaphone: horn (trapezoid) + body + handle, in orange
    orange = "#ff9f43"
    d.polygon(
        [(150 * s, 210 * s), (330 * s, 120 * s), (330 * s, 360 * s), (150 * s, 290 * s)],
        fill=orange,
    )
    d.rounded_rectangle(
        [96 * s, 200 * s, 160 * s, 300 * s], radius=18 * s, fill=orange
    )
    d.rounded_rectangle(
        [140 * s, 290 * s, 190 * s, 390 * s], radius=16 * s, fill=orange
    )
    # Sound waves
    for i, r in enumerate((60, 100, 140)):
        d.arc(
            [(350 - r) * s, (240 - r) * s, (350 + r) * s, (240 + r) * s],
            start=-55, end=55, fill="#f5f0fa", width=max(2, int((16 - 2 * i) * s)),
        )
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        draw_icon(size).save(OUT / f"icon-{size}.png")
        print(f"  ✓ icon-{size}.png")


if __name__ == "__main__":
    main()
