"""Exercise packaged noVNC through a Supervisor-like proxy, without HA or login."""
from html.parser import HTMLParser
from pathlib import Path
import sys
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8080/api/hassio_ingress/test-token_0123456789/'
TYPES = {'.css': {'text/css'}, '.js': {'application/javascript', 'text/javascript'},
         '.svg': {'image/svg+xml'}, '.png': {'image/png'}, '.ico': {'image/x-icon', 'image/vnd.microsoft.icon'}}


def asset(url, content_type, body):
    path = urlsplit(url).path
    assert path.startswith(urlsplit(BASE).path), f'Escaped ingress prefix: {url}'
    relative = path.removeprefix(urlsplit(BASE).path)
    suffix = Path(relative).suffix
    if suffix in TYPES:
        assert content_type.split(';')[0] in TYPES[suffix], f'Wrong MIME for {url}: {content_type}'
        assert body and body == (Path('/usr/share/novnc') / relative).read_bytes(), f'Wrong asset body: {url}'


if '--regression' in sys.argv:
    # The original config served successful CSS responses with text/plain.
    url = 'http://127.0.0.1:8080/api/hassio_ingress/regression-token/app/styles/base.css'
    with urlopen(url, timeout=10) as response:
        assert response.status == 200
        assert response.headers.get_content_type() == 'text/plain'
        assert response.read() == Path('/usr/share/novnc/app/styles/base.css').read_bytes()
    print('Original nginx config reproduced: CSS 200 with text/plain (browser rejects it)', flush=True)
    sys.exit(0)


class Assets(HTMLParser):
    urls = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'img', 'input'} and attrs.get('src'):
            self.urls.add(attrs['src'])
        if tag == 'link' and attrs.get('href'):
            self.urls.add(attrs['href'])


with urlopen(BASE, timeout=10) as response:
    document_url = response.url
    assert document_url == BASE + 'vnc.html?autoconnect=true&resize=remote&path=websockify', document_url
    assert response.headers.get_content_type() == 'text/html'
    parser = Assets()
    parser.feed(response.read().decode())
# Include the module entry point and an imported module, not only script src tags.
for relative in parser.urls | {'app/ui.js', 'core/rfb.js'}:
    url = urljoin(document_url, relative)
    with urlopen(url, timeout=10) as response:
        assert response.status == 200
        asset(url, response.headers['Content-Type'], response.read())
print('Prefixed redirect, all HTML assets, CSS/JS/SVG MIME and exact package bytes: PASS', flush=True)

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox'])
    page = browser.new_page()
    errors, responses, sockets, frames = [], [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('requestfailed', lambda request: errors.append(f'{request.url}: {request.failure}'))
    page.on('response', lambda response: responses.append(response))

    def websocket(ws):
        sockets.append(ws.url)
        ws.on('framereceived', lambda frame: frames.append(frame))
        ws.on('socketerror', lambda error: errors.append(str(error)))

    page.on('websocket', websocket)
    try:
        page.goto(BASE)
        page.wait_for_function("document.documentElement.classList.contains('noVNC_connected')", timeout=30000)
        page.wait_for_function("document.querySelector('#noVNC_container canvas')?.width > 0")
        assert not page.locator('#noVNC_fallback_error').is_visible()
        assert not errors, errors
        assert sockets == [BASE.replace('http:', 'ws:') + 'websockify'], sockets
        assert any(isinstance(frame, bytes) and frame.startswith(b'RFB ') for frame in frames), 'No VNC protocol data'
        seen = set()
        for response in responses:
            if response.status == 302:
                assert response.url == BASE
                assert response.headers['location'] == 'vnc.html?autoconnect=true&resize=remote&path=websockify'
                continue  # Chromium does not retain redirect response bodies.
            # noVNC optionally fetches these absent configuration files.
            if urlsplit(response.url).path.rsplit('/', 1)[-1] in {'defaults.json', 'mandatory.json'}:
                assert response.status in {200, 404}
                continue
            assert response.status < 400, (response.url, response.status)
            asset(response.url, response.headers.get('content-type', ''), response.body())
            seen.add(Path(urlsplit(response.url).path).suffix)
        assert {'.css', '.js', '.svg'} <= seen, seen
        print(f'Chrome rendered noVNC and connected through prefixed WebSocket with RFB data; {len(responses)} responses checked: PASS')
    except Exception:
        print('Browser errors:', errors, 'WebSockets:', sockets, flush=True)
        raise
    finally:
        browser.close()
