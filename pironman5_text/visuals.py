"""GPL-2.0-only. Original monochrome pictograms and frame-based animation.

Community addition, 2026-09-12. No external images, URLs or animation threads.
"""
import math
from PIL import Image, ImageDraw, ImageFont

SIZES = {8: (16, 4), 12: (20, 3), 16: (28, 2), 24: (36, 1)}
ICONS = ('home', 'heart', 'thermometer', 'bulb', 'check', 'warning', 'wifi', 'fan', 'spinner')
ANIMATIONS = ('none', 'blink', 'pulse', 'spin')


def icon_image(name, frame=0, animation='none', size=32):
    image = Image.new('1', (32, 32))
    d = ImageDraw.Draw(image)
    if name == 'home':
        d.line([(2, 15), (16, 3), (29, 15)], fill=1, width=3)
        d.rectangle((7, 14, 25, 28), outline=1, width=2)
        d.rectangle((13, 20, 19, 28), outline=1, width=2)
    elif name == 'heart':
        d.ellipse((3, 5, 17, 19), fill=1)
        d.ellipse((14, 5, 28, 19), fill=1)
        d.polygon([(3, 13), (28, 13), (16, 28)], fill=1)
    elif name == 'thermometer':
        d.rounded_rectangle((12, 2, 20, 23), radius=4, outline=1, width=2)
        d.ellipse((9, 18, 23, 31), fill=1)
        d.line((16, 10, 16, 24), fill=1, width=3)
        d.line((24, 7, 28, 7), fill=1, width=2)
        d.line((24, 13, 28, 13), fill=1, width=2)
    elif name == 'bulb':
        d.ellipse((7, 2, 25, 20), outline=1, width=2)
        d.rectangle((12, 18, 20, 24), outline=1, width=2)
        d.line((12, 27, 20, 27), fill=1, width=2)
        d.line((14, 30, 18, 30), fill=1, width=2)
    elif name == 'check':
        d.line([(3, 17), (12, 26), (29, 6)], fill=1, width=4)
    elif name == 'warning':
        d.line([(16, 2), (30, 28), (2, 28), (16, 2)], fill=1, width=2)
        d.line((16, 11, 16, 19), fill=1, width=3)
        d.rectangle((15, 23, 17, 25), fill=1)
    elif name == 'wifi':
        for box in [(-4, 5, 36, 43), (3, 12, 29, 38), (10, 20, 22, 32)]:
            d.arc(box, 220, 320, fill=1, width=3)
        d.ellipse((14, 27, 18, 31), fill=1)
    elif name in ('fan', 'spinner'):
        angle = (frame % 8) * math.pi / 4 if animation == 'spin' else 0
        if name == 'fan':
            for blade in range(3):
                a = angle + blade * math.tau / 3
                points = [(16, 16)]
                for delta, radius in [(-0.35, 7), (0.0, 14), (0.65, 14), (0.9, 7)]:
                    points.append((round(16 + radius*math.cos(a+delta)), round(16 + radius*math.sin(a+delta))))
                d.polygon(points, fill=1)
            d.ellipse((13, 13, 19, 19), fill=0, outline=1)
        else:
            for step in range(8):
                a = step*math.pi/4
                active = step == frame % 8 if animation == 'spin' else step == 0
                x, y = 16 + 12*math.cos(a), 16 + 12*math.sin(a)
                radius = 3 if active else 1
                d.ellipse((round(x-radius), round(y-radius), round(x+radius), round(y+radius)), fill=1)
    if animation == 'blink' and frame % 2:
        return Image.new('1', (size, size))
    scale = (0.65, 0.8, 1.0, 0.8)[frame % 4] if animation == 'pulse' else 1
    scaled = image.resize((max(1, round(size*scale)),)*2, Image.Resampling.NEAREST)
    result = Image.new('1', (size, size))
    result.paste(scaled, ((size-scaled.width)//2, (size-scaled.height)//2))
    return result


def fit_text(text, font, width):
    def measured(value):
        box = font.getbbox(value)
        return max(font.getlength(value), box[2]-box[0])
    if measured(text) <= width:
        return text
    while text and measured(text + '...') > width:
        text = text[:-1]
    return text + '...'


def render_frame(message, font, frame=0):
    image = Image.new('1', (128, 64))
    text_layer = Image.new('L', (128, 64))
    draw = ImageDraw.Draw(text_layer)
    lines = message['lines']
    icon = message['icon']
    left = 40 if icon and lines else 0
    if icon:
        size = 32 if lines else 48
        xy = (0, 16) if lines else (40, 8)
        image.paste(icon_image(icon, frame, message['animation'], size), xy)
    cell, _ = SIZES[message['font_size']]
    for i, line in enumerate(lines):
        line = fit_text(line, font, 128-left)
        # Normalize font bearing; keep descenders inside their own line cell.
        box = font.getbbox(line)
        height = box[3]-box[1]
        draw.text((left-box[0], i*cell + max(0, (cell-height)//2)-box[1]), line, font=font, fill=255)
    # Threshold grayscale glyphs explicitly; direct mode-1 rendering drops
    # strokes in Minecraftia at non-native sizes such as 12.
    mask = text_layer.point(lambda p: 255 if p >= 100 else 0).convert('1')
    image.paste(1, (0, 0, 128, 64), mask)
    return image


def load_font(driver, size):
    if size == 8:
        return driver.font_8
    if size == 12:
        return driver.font_12
    return ImageFont.truetype(driver.font_8.path, size)
