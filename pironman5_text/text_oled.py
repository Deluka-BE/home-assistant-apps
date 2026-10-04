"""GPL-2.0-only. Temporary text overlay for PM_Auto 1.2.5.

All drawing uses the existing OLED instance and is serialized with an RLock.
The HTTP thread only replaces in-memory text; no sleep or disk write is used.
"""
import math
import threading
import time
from visuals import SIZES, ICONS, ANIMATIONS, load_font, render_frame


def validate_message(body):
    if not isinstance(body, dict) or set(body) - {'lines', 'duration', 'font_size', 'icon', 'animation'}:
        raise ValueError('Expected lines, duration, font_size, icon and/or animation')
    size = body.get('font_size', 8)
    if type(size) is not int or size not in SIZES:
        raise ValueError('font_size must be 8, 12, 16 or 24')
    icon = body.get('icon')
    if icon is not None and (not isinstance(icon, str) or icon not in ICONS):
        raise ValueError('icon must be null or one of: ' + ', '.join(ICONS))
    animation = body.get('animation', 'none')
    if not isinstance(animation, str) or animation not in ANIMATIONS:
        raise ValueError('animation must be none, blink, pulse or spin')
    if animation != 'none' and not icon:
        raise ValueError('An animation requires an icon')
    if animation == 'spin' and icon not in ('fan', 'spinner'):
        raise ValueError('spin requires the fan or spinner icon')
    lines = body.get('lines', [])
    if not isinstance(lines, list) or len(lines) > SIZES[size][1]:
        raise ValueError('font_size %s permits at most %s lines' % (size, SIZES[size][1]))
    if not lines and not icon:
        raise ValueError('Provide text lines or an icon')
    for line in lines:
        if not isinstance(line, str) or len(line) > 80:
            raise ValueError('Each line must be a string of at most 80 characters')
        if any(not (32 <= ord(c) <= 126 or c == '°') for c in line):
            raise ValueError('Use printable ASCII or the degree symbol; no emoji/newlines')
    duration = body.get('duration', 30)
    if type(duration) not in (int, float) or not 0 <= duration <= 86400 or not math.isfinite(duration):
        raise ValueError('duration must be 0 to 86400 seconds; 0 means persistent')
    return {'lines': tuple(lines), 'duration': duration, 'font_size': size,
            'icon': icon, 'animation': animation}


def make_text_oled(base):
    class TextOLED(base):
        def __init__(self, config, *args, **kwargs):
            self._lock = threading.RLock()
            self._message = None
            self._deadline = None
            self._resume_pending = False
            self._clock = time.monotonic
            self._enabled = bool(config.get('oled_enable', True))
            self._fonts = {}
            self._started = 0
            super().__init__(config, *args, **kwargs)

        def _expire(self):
            if self._message is not None and self._deadline is not None and self._clock() >= self._deadline:
                self._message = None
                self._deadline = None
                self._resume_pending = True

        def set_text(self, body):
            message = validate_message(body)
            with self._lock:
                if not self.is_ready():
                    raise RuntimeError('OLED not ready')
                if not self._enabled:
                    raise RuntimeError('OLED disabled: enable it first')
                size = message['font_size']
                if size not in self._fonts:
                    try:
                        self._fonts[size] = load_font(self.oled, size)
                    except Exception as exc:
                        raise RuntimeError('Requested OLED font could not be loaded') from exc
                self._message = message
                self._started = self._clock()
                self._deadline = self._started + message['duration'] if message['duration'] else None
                self._resume_pending = False
                return self.text_status()

        def clear_text(self):
            with self._lock:
                if self._message is not None:
                    self._resume_pending = True
                self._message = None
                self._deadline = None
                return self.text_status()

        def text_status(self):
            with self._lock:
                self._expire()
                return {'active': self._message is not None,
                        'enabled': self._enabled,
                        'lines': list(self._message['lines']) if self._message else [],
                        'font_size': self._message['font_size'] if self._message else 8,
                        'icon': self._message['icon'] if self._message else None,
                        'animation': self._message['animation'] if self._message else 'none',
                        'remaining': max(0, self._deadline - self._clock()) if self._deadline else None}

        def update_config(self, config):
            with self._lock:
                if 'oled_enable' in config:
                    self._enabled = bool(config['oled_enable'])
                    if not self._enabled:
                        self._message = None
                        self._deadline = None
                        self._resume_pending = False
                return super().update_config(config)

        def draw_oled(self):
            with self._lock:
                if not self._enabled:
                    return
                self._expire()
                if self._message is None:
                    return super().draw_oled()
                try:
                    self.oled.clear()
                    # One frame per existing ~1-second PMAuto hardware cycle.
                    frame = max(0, int(self._clock() - self._started))
                    picture = render_frame(self._message, self._fonts[self._message['font_size']], frame)
                    self.oled.image.paste(picture, (0, 0))
                    self.oled.display()
                except Exception:
                    # Rendering failure must not terminate PMAuto's fan loop.
                    self.log.exception('OLED text rendering failed; returning to normal display')
                    self._message = None
                    self._deadline = None
                    self._resume_pending = True

        def run(self):
            with self._lock:
                self._expire()
                if not self._enabled or not self.is_ready():
                    return
                if self._message is not None:
                    self.wake_flag = True
                    self.draw_oled()
                    return
                if self._resume_pending:
                    self._resume_pending = False
                    self.wake_flag = True
                    self.wake_start_time = time.time()
                return super().run()

        def wake(self):
            with self._lock:
                if self._enabled:
                    return super().wake()

        def sleep(self):
            with self._lock:
                return super().sleep()

        def close(self):
            with self._lock:
                return super().close()

    return TextOLED
