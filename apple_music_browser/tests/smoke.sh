#!/bin/sh
set -eu
docker network create --subnet 172.30.32.0/24 apple-test
docker volume create apple-test-data
trap 'docker logs apple-test; docker rm -f apple-test; docker network rm apple-test; docker volume rm apple-test-data' EXIT
docker run -d --name apple-test --network apple-test --ip 172.30.32.3 -v apple-test-data:/data apple-music-test
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
# Use the exact Supervisor source IP; test ingress HTML, WebSocket handshake and MCP.
docker run --rm -i --network apple-test --ip 172.30.32.2 --entrypoint /opt/venv/bin/python apple-music-test - <<'PY'
import json, socket, urllib.request
base='http://172.30.32.3:8099'
assert b'noVNC' in urllib.request.urlopen(base+'/vnc.html').read()
s=socket.create_connection(('172.30.32.3',8099))
s.sendall(b'GET /websockify HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n')
assert b'101' in s.recv(4096).split(b'\r\n')[0]
s.close()
def rpc(method, params):
    req=urllib.request.Request(base+'/mcp',json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
    return json.load(urllib.request.urlopen(req))
assert 'result' in rpc('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'smoke','version':'1'}})
tools=rpc('tools/list',{})['result']['tools']
assert {t['name'] for t in tools} == {'play_song','control','now_playing','volume'}
res=rpc('tools/call',{'name':'play_song','arguments':{'catalog_id':'playlist-test'}})
assert res['result']['isError'] is True
print('Ingress HTML, WebSocket, MCP initialize/list and playlist-input rejection: PASS')
PY
docker exec apple-test sh -c 'test -f /data/browser/chrome/Default/Preferences && touch /data/persistence-test'
docker restart apple-test
docker exec apple-test test -f /data/persistence-test
