#!/bin/sh
set -eu
docker network create --subnet 172.30.32.0/24 apple-test
docker volume create apple-test-data
docker volume create apple-test-ssl
# Random ephemeral CI credentials stay in a private volume, never in logs/arguments.
docker run --rm -v apple-test-data:/data -v apple-test-ssl:/ssl --entrypoint /bin/sh apple-music-test -ec '
  umask 077
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=apple-test \
    -addext "subjectAltName=DNS:apple-test,DNS:host.docker.internal" \
    -keyout /ssl/key.pem -out /ssl/cert.pem 2>/dev/null
 '
docker run --rm -i -v apple-test-data:/data --entrypoint /opt/venv/bin/python apple-music-test - <<'PYOPTIONS'
import json, secrets, os
os.umask(0o077)
json.dump({'mcp_enabled': True, 'mcp_token': secrets.token_urlsafe(48),
           'mcp_certfile': 'cert.pem', 'mcp_keyfile': 'key.pem'}, open('/data/options.json', 'w'))
PYOPTIONS
trap 'docker logs apple-test; docker rm -f apple-test; docker network rm apple-test; docker volume rm apple-test-data apple-test-ssl' EXIT
docker run -d --name apple-test --network apple-test --ip 172.30.32.3 -p 18765:8765 -v apple-test-ssl:/ssl:ro -v apple-test-data:/data apple-music-test
for attempt in $(seq 1 90); do
  if docker exec apple-test /opt/venv/bin/python -c 'import socket; socket.create_connection(("127.0.0.1",8765),1)' 2>/dev/null; then break; fi
  sleep 2
done
# Confirm the production ingress rejects connections outside Supervisor.
docker exec apple-test /opt/venv/bin/python -c 'import urllib.request,urllib.error
try: urllib.request.urlopen("http://127.0.0.1:8099/"); raise AssertionError("Ingress is public")
except urllib.error.HTTPError as e: assert e.code == 403'
# Native ARM64 Chrome must expose the actual Widevine EME capability.
docker exec -i apple-test /opt/venv/bin/python - <<'PY'
import platform
from playwright.sync_api import sync_playwright
assert platform.machine() == 'aarch64', platform.machine()
with sync_playwright() as p:
    b = p.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox'])
    page = b.new_page()
    page.goto('http://localhost:8099')
    result = page.evaluate('''async () => {
      const access = await navigator.requestMediaKeySystemAccess('com.widevine.alpha', [{
        initDataTypes: ['cenc'], audioCapabilities: [{contentType: 'audio/mp4; codecs="mp4a.40.2"'}]
      }]);
      await access.createMediaKeys();
      return access.keySystem;
    }''')
    assert result == 'com.widevine.alpha', result
    print('Native ARM64 Chrome Widevine MediaKeys: PASS')
    b.close()
PY
# Reproduce the old MIME failure on a separate nginx listener, using the same assets.
# The production listener/config and Supervisor allowlist remain intact.
docker exec -i apple-test /opt/venv/bin/python - <<'PYREG'
from pathlib import Path
config = Path('/opt/addon/nginx.conf').read_text()
config = '\n'.join(line for line in config.splitlines()
                   if 'include /etc/nginx/mime.types;' not in line
                   and 'default_type ' not in line)
config = config.replace('listen 8099;', 'listen 8100;').replace('/tmp/nginx.pid', '/tmp/nginx-regression.pid')
Path('/tmp/nginx-regression.conf').write_text(config)
PYREG
docker exec apple-test nginx -c /tmp/nginx-regression.conf
# The browser sees /api/hassio_ingress/<token>/; only the proxy strips it.
# Chrome runs outside the addon, at the exact Supervisor source IP.
docker run --rm --network apple-test --ip 172.30.32.2 \
  -v "$(pwd)/apple_music_browser/tests:/tests:ro" \
  --entrypoint /bin/sh apple-music-test -ec '
    nginx -c /tests/ingress-proxy.conf
    /opt/venv/bin/python /tests/ingress.py --regression
    /opt/venv/bin/python /tests/ingress.py
  '
# Use the exact Supervisor source IP; test ingress HTML, WebSocket handshake and MCP.
docker run --rm -i --network apple-test --ip 172.30.32.2 --entrypoint /opt/venv/bin/python apple-music-test - <<'PY'
import json, socket, urllib.request
base='http://172.30.32.3:8099'
for port in (5900,6080):
    try:
        leaked=socket.create_connection(('172.30.32.3',port),1)
    except OSError:
        continue
    leaked.close()
    raise AssertionError(f'Backend port {port} exposed')
assert b'noVNC' in urllib.request.urlopen(base+'/vnc.html').read()
s=socket.create_connection(('172.30.32.3',8099))
s.sendall(b'GET /websockify HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n')
assert b'101' in s.recv(4096).split(b'\r\n')[0]
s.close()
import urllib.error
try:
    urllib.request.urlopen(base+'/mcp')
    raise AssertionError('Ingress MCP must be removed')
except urllib.error.HTTPError as error:
    assert error.code == 404
print('Ingress HTML, WebSocket, VNC isolation and removed ingress MCP: PASS')
PY
# Docker host publishing models Windows LAN access; sibling DNS models HA addons.
docker run --rm --network apple-test --add-host host.docker.internal:host-gateway \
  -v apple-test-data:/data:ro -v apple-test-ssl:/ssl:ro \
  -v "$(pwd)/apple_music_browser/tests:/tests:ro" \
  --entrypoint /opt/venv/bin/python apple-music-test /tests/mcp_network.py
docker exec apple-test touch /data/persistence-test
docker stop -t 40 apple-test
docker run --rm -v apple-test-data:/data --entrypoint /opt/venv/bin/python apple-music-test -c 'import json,pathlib; assert pathlib.Path("/data/persistence-test").exists(); json.load(open("/data/browser/chrome/Default/Preferences")); print("Profile persisted after clean shutdown: PASS")'
docker start apple-test
for attempt in $(seq 1 60); do
  if docker exec apple-test /opt/venv/bin/python -c 'import socket; socket.create_connection(("127.0.0.1",8765),1)' 2>/dev/null; then
    docker run --rm --network apple-test --add-host host.docker.internal:host-gateway \
      -v apple-test-data:/data:ro -v apple-test-ssl:/ssl:ro \
      -v "$(pwd)/apple_music_browser/tests:/tests:ro" \
      --entrypoint /opt/venv/bin/python apple-music-test /tests/mcp_network.py
    echo 'Authenticated TLS MCP reconnected after persistent-profile restart: PASS'
    docker exec apple-test /opt/venv/bin/python -c 'import json; p="/data/options.json"; o=json.load(open(p)); o["mcp_enabled"]=False; json.dump(o,open(p,"w"))'
    docker restart -t 40 apple-test
    for disabled_attempt in $(seq 1 60); do
      if docker logs apple-test 2>&1 | grep -q 'MCP disabled; browser remains available'; then break; fi
      sleep 2
    done
    docker logs apple-test 2>&1 | grep -q 'MCP disabled; browser remains available'
    docker exec apple-test /opt/venv/bin/python -c 'import socket
s=socket.socket(); s.settimeout(1)
assert s.connect_ex(("127.0.0.1",8765)) != 0, "Disabled MCP is listening"
s.close()'
    docker run --rm --network apple-test --ip 172.30.32.2 --entrypoint /opt/venv/bin/python apple-music-test -c 'import urllib.request; assert b"noVNC" in urllib.request.urlopen("http://172.30.32.3:8099/vnc.html").read()'
    docker stop -t 40 apple-test
    docker run --rm -v apple-test-data:/data:ro --entrypoint /opt/venv/bin/python apple-music-test -c 'import json,pathlib
json.load(open("/data/browser/chrome/Default/Preferences"))
assert not pathlib.Path("/data/browser/chrome/SingletonLock").is_symlink(), "Browser did not close cleanly"
print("Disabled MCP has no listener; ingress works; Chrome closes cleanly with persistent profile: PASS")'
    exit 0
  fi
  sleep 2
done
exit 1
