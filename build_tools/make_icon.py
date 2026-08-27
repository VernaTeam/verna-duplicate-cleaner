# -*- coding: utf-8 -*-
"""ساخت آیکون برنامه (app.ico) بدون نیاز به فایل گرافیکی بیرونی."""

import os

from PIL import Image, ImageDraw

SIZE = 512
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "app.ico")

BG_TOP = (37, 99, 235)
BG_BOTTOM = (29, 78, 216)
CARD_BACK = (191, 219, 254)
CARD_FRONT = (255, 255, 255)
NOTE = (30, 64, 175)
ACCENT = (220, 38, 38)


def rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def build() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # پس‌زمینهٔ گرادیانی
    grad = Image.new("RGB", (1, SIZE))
    gd = ImageDraw.Draw(grad)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        gd.point((0, y), fill=tuple(
            int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)))
    grad = grad.resize((SIZE, SIZE))

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1],
                                           radius=110, fill=255)
    img.paste(grad, (0, 0), mask)
    draw = ImageDraw.Draw(img)

    # دو «کارت» روی هم: نشانهٔ فایل تکراری
    rounded(draw, [96, 84, 356, 424], 34, CARD_BACK)
    rounded(draw, [150, 128, 410, 468], 34, CARD_FRONT)

    # نت موسیقی روی کارت جلویی
    head_r = 42
    for cx, cy in ((214, 372), (330, 344)):
        draw.ellipse([cx - head_r, cy - head_r * 0.78,
                      cx + head_r, cy + head_r * 0.78], fill=NOTE)
    draw.rectangle([214 + head_r - 14, 200, 214 + head_r, 372], fill=NOTE)
    draw.rectangle([330 + head_r - 14, 172, 330 + head_r, 344], fill=NOTE)
    draw.polygon([(214 + head_r - 14, 200), (330 + head_r, 172),
                  (330 + head_r, 214), (214 + head_r - 14, 242)], fill=NOTE)

    # علامت حذف در گوشه
    draw.ellipse([330, 330, 470, 470], fill=ACCENT)
    for a, b, c, d in ((366, 366, 434, 434), (434, 366, 366, 434)):
        draw.line([a, b, c, d], fill="white", width=22)

    return img


def main():
    img = build()
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(OUT, format="ICO", sizes=sizes)
    img.resize((256, 256), Image.LANCZOS).save(
        os.path.splitext(OUT)[0] + ".png")
    print("saved:", OUT)


if __name__ == "__main__":
    main()
