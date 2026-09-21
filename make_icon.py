#!/usr/bin/env python3
"""Generate app_icon.ico (multi-size) for the SSD Temperature Monitor exe."""

from PIL import Image, ImageDraw, ImageFont

SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(size: int) -> Image.Image:
    s = size * 4  # render 4x then downscale for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, s - 4, s - 4], radius=s // 5, fill="#0f172a")
    d.rounded_rectangle([4, 4, s - 4, s - 4], radius=s // 5,
                        outline="#22c55e", width=max(2, s // 22))
    # thermometer bulb
    cx, cy, r = s // 2, int(s * 0.66), s // 7
    d.rectangle([cx - r // 2, int(s * 0.30), cx + r // 2, cy], fill="#22c55e")
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#22c55e")
    # mercury ticks
    for i, frac in enumerate((0.42, 0.52, 0.62)):
        y = int(s * frac)
        d.line([cx + r, y, cx + int(s * 0.14), y], fill="#64748b",
               width=max(1, s // 60))
    try:
        font = ImageFont.truetype("arialbd.ttf", int(s * 0.30))
    except OSError:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), "42", font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((cx - w / 2 - bbox[0], s * 0.06), "42", font=font, fill="white")
    return img.resize((size, size), Image.LANCZOS)


def main():
    imgs = [render(sz) for sz in SIZES]
    imgs[-1].save("app_icon.ico", format="ICO",
                  sizes=[(sz, sz) for sz in SIZES],
                  append_images=imgs[:-1])
    print("app_icon.ico written")


if __name__ == "__main__":
    main()
