"""Run from a sibling container: exercise internal DNS and host-published TLS."""
import asyncio
import json
import ssl
from pathlib import Path
import urllib.request
import urllib.error
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

token = json.loads(Path('/data/options.json').read_text())['mcp_token']
context = ssl.create_default_context(cafile='/ssl/cert.pem')


def request(base, method='POST', auth=None, extra=None, path='/mcp', payload=None):
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
    if auth is not None:
        headers['Authorization'] = auth
    headers.update(extra or {})
    req = urllib.request.Request(base + path, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=context, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


async def main():
    for base in ('https://apple-test:8765', 'https://host.docker.internal:18765'):
        for method in ('GET', 'POST', 'DELETE', 'OPTIONS'):
            for auth in (None, 'Bearer wrong', 'Basic wrong'):
                assert request(base, method, auth)[0] == 401
        auth = 'Bearer ' + token
        assert request(base, auth=auth, extra={'Origin': 'http://localhost:6274'})[0] == 403
        assert request(base, auth=auth, path='/vnc.html')[0] == 404
        assert request(base, auth=auth, path='/mcp?token=ignored')[0] == 404
        assert request(base, 'GET', auth)[0] == 405  # Stateless: no standalone SSE.
        assert request(base, 'DELETE', auth)[0] == 405
        # Declare oversize before uploading: server must reject from headers.
        # Sending the full body races TLS close and can hide the 413 as BrokenPipe.
        assert request(base, auth=auth, extra={'Content-Length': '65537'}, payload=b'')[0] == 413
        for version in ('2025-03-26', '2025-06-18', '2025-11-25'):
            status, body = request(base, auth=auth, payload=json.dumps({
                'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                    'protocolVersion': version, 'capabilities': {},
                    'clientInfo': {'name': 'network-test', 'version': '1'}}}).encode())
            assert status == 200 and json.loads(body)['result']['protocolVersion'] == version
        async with httpx.AsyncClient(verify=context, headers={'Authorization': auth}, trust_env=False) as client:
            async with streamable_http_client(base + '/mcp', http_client=client) as (read, write, session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await session.send_ping()
                    assert session_id() is None
                    tools = await session.list_tools()
                    assert {t.name for t in tools.tools} == {'play_song', 'control', 'now_playing', 'volume', 'list_playlists', 'playlist_tracks', 'search_library', 'search_catalog', 'create_playlist', 'rename_playlist', 'add_playlist_tracks', 'remove_playlist_track', 'delete_playlist', 'list_playlist_folders', 'create_playlist_folder', 'move_playlist_to_folder', 'search_playlist_tracks', 'copy_playlist', 'replace_playlist_track', 'catalog_identifiers', 'resolve_isrc', 'album_tracks', 'artist_discovery', 'catalog_genres', 'catalog_suggestions', 'browse_library', 'library_history', 'music_recommendations', 'music_charts', 'account_storefront', 'session_diagnostics'}
                    assert not (await session.call_tool('now_playing', {})).isError
                    assert (await session.call_tool('play_song', {'catalog_id': 'playlist-test'})).isError
                    # Invalid migration inputs must fail before any Apple request.
                    assert (await session.call_tool('resolve_isrc', {'isrcs': ['invalid']})).isError
                    assert (await session.call_tool('catalog_identifiers', {'identifiers': ['i.library']})).isError
        print('TLS auth, protocol negotiation, SDK initialize/ping/list/call: PASS (' + base + ')')

asyncio.run(main())
