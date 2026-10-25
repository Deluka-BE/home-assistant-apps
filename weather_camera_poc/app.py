"""Ingress-only diagnostics and serialized in-memory A/B analysis."""
import copy
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, jsonify, request
from PIL import Image
from models import CANDIDATES, normalize

MAX_BYTES = 8 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 16_000_000
DEFAULTS = dict(camera_entity='camera.woonkamer_vloeiend', daylight_only=True,
                interval_minutes=60, minimum_confidence=0.65,
                save_history=True, history_max_images=336)
SENSORS = {name: 'sensor.weather_camera_' + name for name in CANDIDATES}


class HA:
    def __init__(self):
        self.token = os.environ['SUPERVISOR_TOKEN']

    def get(self, path, image=False):
        # Fixed origin, no redirects, proxy environment, camera token or user URL.
        with requests.Session() as session:
            session.trust_env = False
            with session.get('http://supervisor/core/api/' + path,
                             headers={'Authorization': 'Bearer ' + self.token},
                             timeout=(5, 20), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise RuntimeError('Home Assistant request failed')
                chunks, size = [], 0
                deadline = time.monotonic() + 30
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > (MAX_BYTES if image else 65536) or time.monotonic() > deadline:
                        raise RuntimeError('Home Assistant response limit exceeded')
                    chunks.append(chunk)
                data = b''.join(chunks)
                return data if image else json.loads(data)

    def daylight(self):
        return self.get('states/sun.sun').get('state') == 'above_horizon'

    def publish_sensor(self, name, state, attributes):
        # Only the two fixed IDs are writable; never follow redirects with the token.
        with requests.Session() as session:
            session.trust_env = False
            with session.post('http://supervisor/core/api/states/' + SENSORS[name],
                              headers={'Authorization': 'Bearer ' + self.token},
                              json={'state': state, 'attributes': attributes},
                              timeout=(5, 20), allow_redirects=False, stream=True) as response:
                if response.status_code not in (200, 201):
                    raise RuntimeError('Home Assistant sensor update failed')

    def snapshot(self, entity):
        return self.get('camera_proxy/' + entity, image=True)


def run_candidate(name, data):
    environment = dict(os.environ)
    environment.pop('SUPERVISOR_TOKEN', None)
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('worker.py')), name],
                            input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=900, check=True, env=environment)
    return json.loads(result.stdout)


class Analysis:
    def __init__(self, options, ha, runner=run_candidate, history_dir=Path('/data/history')):
        self.options = DEFAULTS | options
        o = self.options
        if not re.fullmatch(r'camera\.[a-z0-9_]{1,128}', o['camera_entity']):
            raise ValueError('Invalid camera entity')
        if (type(o['save_history']) is not bool or
                type(o['history_max_images']) is not int or not 24 <= o['history_max_images'] <= 5000 or
                type(o['daylight_only']) is not bool or
                type(o['interval_minutes']) is not int or not 1 <= o['interval_minutes'] <= 1440 or
                type(o['minimum_confidence']) not in (int, float) or
                not 0 <= o['minimum_confidence'] <= 1):
            raise ValueError('Invalid options')
        self.ha, self.runner = ha, runner
        self.history_dir = Path(history_dir)
        self.lock = threading.Lock()
        self.state_lock = threading.RLock()
        self.next_due = 0
        self.state = dict(camera_entity=o['camera_entity'], options=o, busy=False,
                          last_analysis=None, last_attempt=None, results={}, summary=None,
                          error=None, skipped=None, history_error=None,
                          sensor_publications={},
                          history_status='idle' if o['save_history'] else 'disabled',
                          models={k: dict(repository=v[0], revision=v[1], loaded=False,
                                          phase='not_loaded') for k, v in CANDIDATES.items()})

    def status(self):
        with self.state_lock:
            return copy.deepcopy(self.state)

    def update(self, **values):
        with self.state_lock:
            self.state.update(values)

    def analyze(self, bypass=False):
        try:
            if self.options['daylight_only'] and not bypass and not self.ha.daylight():
                self.update(skipped='sun.sun is not above_horizon')
                return
            self.update(results={}, summary=None, skipped=None, error=None)
            data = self.ha.snapshot(self.options['camera_entity'])
            if len(data) > MAX_BYTES:
                raise ValueError('Snapshot too large')
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.width * image.height > Image.MAX_IMAGE_PIXELS:
                        raise ValueError('Too many pixels')
                    image_format = image.format.lower()
                    extension = {'jpeg': 'jpg', 'tiff': 'tif'}.get(image_format, image_format)
                    if not re.fullmatch(r'[a-z0-9]+', extension):
                        raise ValueError('Invalid image format')
                    image.verify()
            results = {}
            for name in CANDIDATES:
                with self.state_lock:
                    self.state['models'][name].update(phase='loading_or_running', loaded=None)
                try:
                    raw = self.runner(name, data)
                    results[name] = normalize(raw.pop('probabilities'),
                                              self.options['minimum_confidence']) | raw
                except Exception:
                    results[name] = {'error': 'Candidate failed; check cache/network and runtime compatibility'}
                finally:
                    with self.state_lock:
                        self.state['models'][name].update(phase='unloaded', loaded=False)
                self.update(results=copy.deepcopy(results))
                self.publish_sensor(name, results[name])
            valid = {k: v for k, v in results.items() if 'error' not in v}
            match = (len(valid) == 2 and len({v['normalized_state'] for v in valid.values()}) == 1
                     and all(v['normalized_state'] != 'unknown' for v in valid.values()))
            winner = max(valid, key=lambda k: valid[k]['confidence']) if len(valid) == 2 else None
            completed = datetime.now(timezone.utc)
            self.update(last_analysis=completed.isoformat(),
                        summary=dict(match=match, higher_confidence=winner,
                                     note='Confidence comparison is not an accuracy verdict'))
            self.save_history(data, extension, completed)
        except Exception:
            self.update(error='Analysis failed: snapshot, sun state or image unavailable/invalid',
                        summary=None)
        finally:
            self.update(busy=False)
            self.lock.release()

    def publish_sensor(self, name, result):
        timestamp = datetime.now(timezone.utc).isoformat()
        attributes = {key: result.get(key) for key in (
            'native_label', 'mapped_state', 'confidence', 'inference_seconds',
            'load_and_inference_seconds', 'peak_rss_bytes')}
        repository, revision = CANDIDATES[name]
        attributes.update(friendly_name='Weather Camera ' + name,
                          last_analysis=timestamp, camera_entity=self.options['camera_entity'],
                          repository=repository, revision=revision)
        if 'error' in result:
            attributes['error'] = result['error']
        try:
            self.ha.publish_sensor(name, result.get('normalized_state', 'unknown'), attributes)
            publication = dict(entity_id=SENSORS[name], last_published=timestamp, error=None)
        except Exception:
            # Publishing must not discard inference/history or skip the next candidate.
            publication = dict(entity_id=SENSORS[name], error='Home Assistant sensor update failed')
        with self.state_lock:
            self.state['sensor_publications'][name] = publication

    def save_history(self, data, extension, completed):
        if not self.options['save_history']:
            self.update(history_status='disabled', history_error=None)
            return
        try:
            images = self.history_dir / 'images'
            images.mkdir(parents=True, exist_ok=True)
            # Microseconds avoid collisions between quick manual analyses/restarts.
            filename = completed.strftime('%Y%m%dT%H%M%S%fZ') + '.' + extension
            target = images / filename
            created = False
            try:
                with target.open('xb') as handle:
                    created = True
                    handle.write(data)
            except Exception:
                if created:
                    target.unlink(missing_ok=True)
                raise
            state = self.status()
            record = dict(timestamp=completed.isoformat(), image_filename=filename,
                          camera_entity=self.options['camera_entity'],
                          results=state['results'], summary=state['summary'])
            with (self.history_dir / 'analysis.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, separators=(',', ':'), allow_nan=False) + '\n')
            # Only prune our timestamp-named images; preserve the append-only audit.
            saved = sorted(p for p in images.iterdir() if p.is_file() and
                           re.fullmatch(r'\d{8}T\d{12}Z\.[a-z0-9]+', p.name))
            for old in saved[:-self.options['history_max_images']]:
                old.unlink()
            self.update(history_status='saved', history_error=None)
        except Exception:
            # Never expose filesystem exception payloads or fail the analysis.
            self.update(history_status='error', history_error='History save or retention failed')

    def start(self, bypass=False):
        if not self.lock.acquire(blocking=False):
            return False
        self.update(busy=True, last_attempt=datetime.now(timezone.utc).isoformat(),
                    error=None, skipped=None)
        threading.Thread(target=self.analyze, args=(bypass,), daemon=True).start()
        return True

    def tick(self, now):
        if now >= self.next_due:
            if self.start():
                self.next_due = now + self.options['interval_minutes'] * 60


def create_app(engine, ingress_only=False):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 1024

    @app.before_request
    def ingress_peer():
        if ingress_only and request.remote_addr != '172.30.32.2':
            return jsonify(error='Ingress access required'), 403

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'self'"
        return response

    @app.get('/')
    def index():
        return '''<!doctype html><html><head><meta charset="utf-8"><title>Weather Camera PoC</title></head>
<body><h1>Weather Camera PoC</h1><p>Sequential CPU A/B comparison. Image history follows save_history settings.</p>
<label><input type="checkbox" id="bypass">Explicitly bypass daylight restriction</label>
<button id="analyze">Analyze Now</button><p id="message"></p><pre id="status"></pre>
<script>
async function refresh(){try{const r=await fetch('api/status');
document.getElementById('status').textContent=JSON.stringify(await r.json(),null,2);
}catch(e){document.getElementById('message').textContent='Status unavailable';}}
document.getElementById('analyze').onclick=async()=>{
const r=await fetch('api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({bypass_daylight:document.getElementById('bypass').checked})});
document.getElementById('message').textContent=r.status===202?'Analysis requested':'Busy or request rejected';refresh();};
refresh();setInterval(refresh,3000);
</script></body></html>'''

    @app.get('/api/status')
    def status():
        return jsonify(engine.status())

    @app.post('/api/analyze')
    def analyze():
        # Ingress supplies authentication; JSON-only prevents cross-origin HTML form posts.
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) - {'bypass_daylight'} or type(body.get('bypass_daylight', False)) is not bool:
            return jsonify(error='Expected JSON with optional boolean bypass_daylight'), 400
        started = engine.start(body.get('bypass_daylight', False))
        return jsonify(accepted=started), 202 if started else 409

    return app


def main():
    with open('/data/options.json', encoding='utf-8') as handle:
        options = json.load(handle)
    engine = Analysis(options, HA())

    def schedule():
        while True:
            engine.tick(time.monotonic())
            time.sleep(10)
    threading.Thread(target=schedule, daemon=True).start()
    # No host port exposed. Access through authenticated Supervisor Ingress only.
    create_app(engine, ingress_only=True).run(host='0.0.0.0', port=8099, threaded=True, debug=False)


if __name__ == '__main__':
    main()
