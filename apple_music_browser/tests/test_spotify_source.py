"""Offline embed fixtures and bounded HTTP transport checks."""
import copy
import json
import io
import http.client
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import spotify_source as source

TARGET = source.DEFAULT_PLAYLIST


def entity(count=30):
    return dict(uri='spotify:playlist:' + TARGET, name='Discover Weekly', type='playlist',
                trackList=[dict(uri=f'spotify:track:{n:022d}', title=f'Track {n}',
                                subtitle='Artist, Guest & Friend', duration=192000, isPlayable=True)
                           for n in range(count)])


def html(value=None, moved=False):
    value = entity() if value is None else value
    payload = {'moved': value} if moved else {'props': {'pageProps': {'state': {'data': {'entity': value}}}}}
    return '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + '</script></html>'


class SourceTests(unittest.TestCase):
    def parse(self, value=None, moved=False):
        return source.parse_embed(html(value, moved), TARGET)

    def assert_bad(self, value, reason=None):
        with self.assertRaises(source.SourceError) as raised:
            self.parse(value)
        if reason:
            self.assertEqual(str(raised.exception), reason)

    def run_reader(self, pages=None):
        with patch.object(source, 'fetch_embed', side_effect=pages or [html(), html()]), patch.object(source.time, 'sleep'):
            return source.read_spotify_discover_weekly()

    def test_success_and_fingerprint(self):
        fixture = (Path(__file__).parent / 'fixtures' / 'spotify_embed.html').read_text(encoding='utf-8')
        result = self.run_reader([fixture, fixture])
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['fingerprint_version'], 2)
        self.assertEqual(result['fingerprint'], self.run_reader()['fingerprint'])
        self.assertEqual(result['completeness_basis'], 'embedded_tracklist')
        track = result['tracks'][0]
        self.assertEqual(track['artists'], ['Artist, Guest & Friend'])
        self.assertEqual(track['artists_source'], 'display_credit')
        self.assertEqual(track['duration_ms'], 192000)

    def test_fallback(self):
        self.assertEqual(self.parse(moved=True)['parser'], 'semantic_json')
        self.assertEqual(self.parse()['parser'], 'next_data')
        # Arbitrary URI strings and non-JSON scripts are never fallback tracks.
        for value in ('<script>spotify:track:' + 'a'*22 + '</script>', '<script type="application/json">{}</script>'):
            with self.assertRaises(source.SourceError):
                source.parse_embed(value, TARGET)

    def test_bad_json(self):
        for text in ('{bad', '', '{"x":'):
            with self.assertRaises(source.SourceError):
                source.parse_embed('<script type="application/json">' + text + '</script>', TARGET)

    def test_conflicts_and_known_identity(self):
        other = entity()
        other['trackList'][0]['title'] = 'Changed'
        page = html() + html(other, True)
        with self.assertRaisesRegex(source.SourceError, 'conflicting_candidates'):
            source.parse_embed(page, TARGET)
        for field, value in [('uri', 'spotify:playlist:' + 'x'*22), ('id', 'x'*22), ('type', 'album'), ('name', 'Other')]:
            bad = entity()
            bad[field] = value
            with self.assertRaises(source.SourceError):
                source.parse_embed(html(bad) + html(entity(), True), TARGET)

    def test_counts_order_and_duplicates(self):
        for count in (29, 30, 31):
            result = self.parse(entity(count))
            self.assertEqual(len(result['tracks']), count)
            self.assertEqual(bool(result['warnings']), count != 30)
        self.assert_bad(entity(0))
        self.assert_bad(entity(1001))
        item = entity()
        item['trackList'][1] = copy.deepcopy(item['trackList'][0])
        item['trackList'].reverse()
        tracks = self.parse(item)['tracks']
        self.assertEqual([t['position'] for t in tracks], list(range(1,31)))
        self.assertEqual(tracks[-1]['id'], tracks[-2]['id'])
        self.assertEqual(tracks[0]['title'], 'Track 29')

    def test_track_validation(self):
        for key, value in [('uri', None), ('uri', 'spotify:episode:'+'a'*22),
                           ('uri', 'spotify:local:artist:song'), ('uri', 'spotify:track:bad'),
                           ('id', 'a'*22), ('type', 'episode'), ('isLocal', True),
                           ('title', ''), ('title', None), ('subtitle', ''), ('subtitle', None),
                           ('artists', []), ('artists', ['']), ('artists', [{'name': ''}]),
                           ('duration', True), ('duration', 0), ('duration', -1),
                           ('duration', '192000'), ('duration', None), ('isPlayable', 'false')]:
            with self.subTest(key=key, value=value):
                item = entity()
                item['trackList'][0][key] = value
                self.assert_bad(item)
        for key in ('uri', 'title', 'subtitle'):
            item = entity()
            del item['trackList'][0][key]
            self.assert_bad(item)
        item = entity()
        del item['trackList'][0]['duration']
        item['trackList'][0]['artists'] = [{'name': 'One'}, {'name': 'Two'}]
        item['trackList'][0]['isPlayable'] = False
        result = self.parse(item)
        self.assertNotIn('duration_ms', result['tracks'][0])
        self.assertEqual(result['tracks'][0]['artists_source'], 'structured')
        self.assertIn('unplayable_track', result['warnings'])

    def test_limits(self):
        pages = ['<script></script>' * 65, html({'deep': [[[[[]]]]]}),
                 '<script type="application/json">' + json.dumps([0]*20001) + '</script>']
        with patch.object(source, 'MAX_DEPTH', 3):
            for page in pages:
                with self.assertRaises(source.SourceError):
                    source.parse_embed(page, TARGET)
        with patch.object(source, 'MAX_BODY', 10):
            with self.assertRaisesRegex(source.SourceError, 'body_too_large'):
                self.parse()

    def test_unstable_no_partial_output(self):
        item = entity()
        item['trackList'].reverse()
        result = self.run_reader([html(), html(item)])
        self.assertEqual(result['status'], 'source_unstable')
        self.assertNotIn('tracks', result)
        self.assertNotIn('fingerprint', result)
        self.assertEqual(self.run_reader([html(), html(entity(29))])['status'], 'source_unstable')
        item = entity()
        item['uri'] = 'spotify:playlist:' + 'x'*22
        self.assertEqual(self.run_reader([html(), html(item)])['status'], 'source_unstable')

    def test_failure_and_lock(self):
        result = self.run_reader([RuntimeError('private token URL')])
        self.assertNotIn('private', str(result))
        self.assertEqual(result['status'], 'source_incomplete')
        self.assertEqual(self.run_reader()['status'], 'ok')
        with source._LOCK:
            self.assertEqual(source.read_spotify_discover_weekly()['status'], 'source_busy')

    def test_urls(self):
        for value in (TARGET, 'https://open.spotify.com/playlist/'+TARGET+'?si=abc&foo=1'):
            self.assertEqual(source.playlist_id(value), TARGET)
        for value in (None, '', 'a'*23, ' '+TARGET, TARGET+'\n',
                      'http://open.spotify.com/playlist/', 'https://evil.com/playlist/',
                      'https://user@open.spotify.com/playlist/', 'https://open.spotify.com:443/playlist/',
                      'https://open.spotify.com.evil/playlist/', 'https://OPEN.SPOTIFY.COM/playlist/',
                      'https://open.spotify.com/playlist/%41', 'https://open.spotify.com/playlist/../playlist/',
                      'https://open.spotify.com/playlist/'+TARGET+'/',
                      'https://open.spotify.com/playlist/'+TARGET+'#x',
                      'https://open.spotify.com/playlist/'+TARGET+'?x=\tsecret'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                source.playlist_id(value)

    def transport(self, chunks=None, status=200, content_type='text/html; charset=utf-8', encoding='identity', error=None):
        conn, response = Mock(), Mock()
        conn.getresponse.return_value = response
        response.length = None
        response.status = status
        response.getheader.side_effect = lambda name, default='': {'Content-Type': content_type, 'Content-Encoding': encoding}.get(name, default)
        response.read1.side_effect = chunks or [b'html', b'']
        if error:
            conn.request.side_effect = error
        with patch.object(source.http.client, 'HTTPSConnection', return_value=conn) as factory:
            try:
                result = source.fetch_embed(TARGET)
            finally:
                conn.close.assert_called_once()
        factory.assert_called_once_with('open.spotify.com', timeout=5)
        args = conn.request.call_args
        self.assertEqual(args.args[1], '/embed/playlist/' + TARGET)
        self.assertNotIn('Cookie', args.kwargs['headers'])
        return result

    def test_transport(self):
        self.assertEqual(self.transport(), 'html')
        for kwargs, reason in [({'status':302}, 'redirect'), ({'status':403}, 'http_status'),
                               ({'content_type':'application/json'}, 'content_type'),
                               ({'encoding':'gzip'}, 'content_encoding'),
                               ({'error':TimeoutError('secret')}, 'timeout'),
                               ({'error':OSError('secret')}, 'network_or_encoding'),
                               ({'chunks':[b'x'*(source.MAX_BODY+1)]}, 'body_too_large')]:
            with self.subTest(kwargs=list(kwargs)), self.assertRaisesRegex(source.SourceError, '^'+reason+'$'):
                self.transport(**kwargs)
        # Small streamed chunks exceed the cap regardless of Content-Length.
        with patch.object(source, 'MAX_BODY', 8):
            with self.assertRaisesRegex(source.SourceError, 'body_too_large'):
                self.transport(chunks=[b'1234', b'5678', b'9'])
        with patch.object(source.time, 'monotonic', side_effect=[0, 0, 0, 16]):
            with self.assertRaisesRegex(source.SourceError, 'timeout'):
                self.transport()

    def test_stalled_transport_is_bounded_without_parallel_retry(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(_):
            entered.set()
            release.wait(2)
            return 'late snapshot'
        with patch.object(source, '_fetch_embed', side_effect=blocked), patch.object(source, 'WORK_TIMEOUT', 0.01):
            try:
                with self.assertRaisesRegex(source.SourceError, '^timeout$'):
                    source.fetch_embed(TARGET)
                self.assertTrue(entered.is_set())
                with self.assertRaisesRegex(source.SourceError, '^transport_busy$'):
                    source.fetch_embed(TARGET)
            finally:
                release.set()
        # Wait for the outstanding worker to release its guard before other tests.
        self.assertTrue(source._FETCH_LOCK.acquire(timeout=2))
        source._FETCH_LOCK.release()

    def test_real_http_framing_and_connection_close(self):
        # Exercise real HTTPResponse framing, including Connection: close and
        # length exhaustion, rather than relying only on response mocks.
        for declared, data, expected in [(4, b'html', 'html'), (8, b'html', 'truncated_body')]:
            raw = (f'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: {declared}\r\n\r\n').encode() + data
            transport = Mock()
            transport.makefile.return_value = io.BytesIO(raw)
            response = http.client.HTTPResponse(transport)
            response.begin()
            conn = Mock(sock=transport)
            conn.getresponse.return_value = response
            with patch.object(source.http.client, 'HTTPSConnection', return_value=conn):
                if expected == 'html':
                    self.assertEqual(source.fetch_embed(TARGET), 'html')
                else:
                    with self.assertRaisesRegex(source.SourceError, expected):
                        source.fetch_embed(TARGET)
            self.assertTrue(response.isclosed())
