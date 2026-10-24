"""Fail-closed reader for Spotify's public embed hydration data (not an API)."""
import hashlib
import http.client
import json
import re
import socket
import threading
import time
from datetime import datetime, timezone
from html.parser import HTMLParser

DEFAULT_PLAYLIST = '37i9dQZEVXcT3aus8zyY7o'
_ID = re.compile(r'[A-Za-z0-9]{22}')
_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()
MAX_BODY = 2 * 1024 * 1024
MAX_SCRIPTS = 64
MAX_NODES = 20000
MAX_DEPTH = 32
PACE = 0.75
WORK_TIMEOUT = 16


class SourceError(Exception):
    """Only fixed, public reason codes may cross the reader boundary."""


def playlist_id(value):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Invalid Spotify playlist input')
    if _ID.fullmatch(value):
        return value
    match = re.fullmatch(r'https://open\.spotify\.com/playlist/([A-Za-z0-9]{22})(?:\?[^#]*)?', value)
    if not match:
        raise ValueError('Invalid Spotify playlist input')
    return match[1]


def fetch_embed(target):
    """Bound caller latency even if the platform DNS resolver stalls.

    At most one daemon transport worker can remain outstanding. A timed-out
    worker never starts a second request or contributes a later snapshot.
    """
    if not _FETCH_LOCK.acquire(blocking=False):
        raise SourceError('transport_busy')
    complete = threading.Event()
    result = []

    def run():
        try:
            result.append(_fetch_embed(target))
        except Exception as error:
            result.append(error if isinstance(error, SourceError) else SourceError('reader_error'))
        finally:
            _FETCH_LOCK.release()
            complete.set()

    worker = threading.Thread(target=run, daemon=True)
    try:
        worker.start()
    except Exception:
        _FETCH_LOCK.release()
        raise SourceError('reader_error') from None
    if not complete.wait(WORK_TIMEOUT):
        raise SourceError('timeout')
    if isinstance(result[0], SourceError):
        raise result[0]
    return result[0]


def _fetch_embed(target):
    """One fresh TLS connection, no cookies, redirects, credentials or retries."""
    deadline = time.monotonic() + 15
    conn = http.client.HTTPSConnection('open.spotify.com', timeout=5)
    transport = None
    response = None

    def interrupt():
        # A socket timeout alone does not bound trickling HTTP headers. Interrupt
        # the underlying socket at the overall deadline as well.
        active = transport if transport is not None else conn.sock
        if active is not None:
            try:
                active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    timer = threading.Timer(15, interrupt)
    timer.daemon = True
    timer.start()
    try:
        conn.connect()
        if time.monotonic() >= deadline:
            raise SourceError('timeout')
        conn.request('GET', '/embed/playlist/' + target, headers={
            'User-Agent': 'home-assistant-apps/spotify-embed-reader',
            'Accept': 'text/html', 'Accept-Encoding': 'identity',
            'Cache-Control': 'no-cache, no-store', 'Pragma': 'no-cache'})
        conn.sock.settimeout(min(5, max(0.001, deadline - time.monotonic())))
        transport = conn.sock
        response = conn.getresponse()
        if 300 <= response.status < 400:
            raise SourceError('redirect')
        if response.status != 200:
            raise SourceError('http_status')
        if response.getheader('Content-Type', '').split(';')[0].strip().lower() != 'text/html':
            raise SourceError('content_type')
        if response.getheader('Content-Encoding', 'identity').lower().strip() != 'identity':
            raise SourceError('content_encoding')
        body = bytearray()
        while True:
            if response.length == 0:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceError('timeout')
            # read1 performs at most one buffered/raw read; a trickling peer cannot
            # keep a single read alive indefinitely beyond the total deadline.
            transport.settimeout(min(5, remaining))
            chunk = response.read1(min(65536, MAX_BODY + 1 - len(body)))
            if not chunk:
                if response.length is not None and response.length > 0:
                    raise SourceError('truncated_body')
                break
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise SourceError('body_too_large')
        if time.monotonic() > deadline:
            raise SourceError('timeout')
        return bytes(body).decode('utf-8', errors='strict')
    except (TimeoutError, socket.timeout):
        raise SourceError('timeout') from None
    except (OSError, http.client.HTTPException, UnicodeError):
        raise SourceError('network_or_encoding') from None
    finally:
        timer.cancel()
        if response is not None:
            response.close()
        conn.close()


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.current = None
        self.count = 0
        self.size = 0

    def handle_starttag(self, tag, attrs):
        if tag != 'script':
            return
        self.count += 1
        if self.count > MAX_SCRIPTS:
            raise SourceError('script_limit')
        attrs = dict(attrs)
        if attrs.get('type', '').lower() == 'application/json':
            self.current = [attrs.get('id'), []]

    def handle_data(self, data):
        if self.current is not None:
            self.size += len(data.encode('utf-8'))
            if self.size > MAX_BODY:
                raise SourceError('json_limit')
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            self.scripts.append((self.current[0], ''.join(self.current[1])))
            self.current = None


def nodes(payload):
    stack = [(payload, 0)]
    visited = 0
    while stack:
        item, depth = stack.pop()
        visited += 1
        if visited > MAX_NODES or depth > MAX_DEPTH:
            raise SourceError('traversal_limit')
        yield item
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)


def normalize(entity, target):
    if not isinstance(entity, dict):
        raise SourceError('playlist_shape')
    identities = [entity[k] for k in ('id', 'uri') if k in entity]
    if not identities or any(v not in (target, 'spotify:playlist:' + target) for v in identities):
        raise SourceError('playlist_identity')
    if entity.get('type', 'playlist') != 'playlist' or entity.get('name') != 'Discover Weekly':
        raise SourceError('playlist_type_or_name')
    # Only the observed embed trackList array is supported; never harvest URIs.
    rows = entity.get('trackList')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise SourceError('track_count')
    tracks, warnings = [], set()
    if len(rows) != 30:
        warnings.add('non_30_track_count')
    for position, row in enumerate(rows, 1):
        if not isinstance(row, dict) or row.get('type', 'track') != 'track' or row.get('isLocal', False):
            raise SourceError('unsupported_track')
        uri = row.get('uri')
        if not isinstance(uri, str) or not re.fullmatch(r'spotify:track:[A-Za-z0-9]{22}', uri):
            raise SourceError('track_uri')
        ident = uri.split(':')[2]
        if 'id' in row and row['id'] != ident:
            raise SourceError('track_id')
        title = row.get('title')
        if not isinstance(title, str) or not title.strip():
            raise SourceError('track_title')
        if 'artists' in row:
            artists = row['artists']
            if not isinstance(artists, list) or not artists:
                raise SourceError('track_artists')
            artists = [a.get('name') if isinstance(a, dict) else a for a in artists]
            provenance = 'structured'
        else:
            artists = [row.get('subtitle')]
            provenance = 'display_credit'
        if any(not isinstance(a, str) or not a.strip() for a in artists):
            raise SourceError('track_artists')
        track = dict(position=position, id=ident, title=title.strip(),
                     artists=[a.strip() for a in artists], artists_source=provenance)
        if 'duration' in row:
            duration = row['duration']
            if type(duration) is not int or duration <= 0:
                raise SourceError('track_duration')
            track['duration_ms'] = duration
        if 'isPlayable' in row:
            if type(row['isPlayable']) is not bool:
                raise SourceError('track_playability')
            track['is_playable'] = row['isPlayable']
            if not row['isPlayable']:
                warnings.add('unplayable_track')
        tracks.append(track)
    return tracks, sorted(warnings)


def parse_embed(html, target):
    if len(html.encode('utf-8')) > MAX_BODY:
        raise SourceError('body_too_large')
    parser = Scripts()
    parser.feed(html)
    parser.close()
    payloads, known = [], []
    total_nodes = 0
    try:
        for script_id, text in parser.scripts:
            payload = json.loads(text)
            # Validate limits even on the known path before normalization.
            total_nodes += sum(1 for _ in nodes(payload))
            if total_nodes > MAX_NODES:
                raise SourceError('traversal_limit')
            payloads.append(payload)
            if script_id == '__NEXT_DATA__':
                entity = payload
                for key in ('props', 'pageProps', 'state', 'data', 'entity'):
                    if not isinstance(entity, dict) or key not in entity:
                        break
                    entity = entity[key]
                else:
                    known.append(entity)
    except (ValueError, RecursionError):
        raise SourceError('invalid_json') from None
    candidates = []
    for payload in payloads:
        for item in nodes(payload):
            if isinstance(item, dict) and (item.get('id') == target or item.get('uri') == 'spotify:playlist:' + target) and 'trackList' in item:
                candidates.append(item)
    # Known entities must validate, even when another candidate looks convenient.
    normalized = [normalize(e, target) for e in known]
    normalized.extend(normalize(e, target) for e in candidates)
    if not normalized:
        raise SourceError('playlist_missing')
    if any(n != normalized[0] for n in normalized[1:]):
        raise SourceError('conflicting_candidates')
    tracks, warnings = normalized[0]
    return dict(tracks=tracks, warnings=warnings, parser='next_data' if known else 'semantic_json')


def read_spotify_discover_weekly(playlist: str = DEFAULT_PLAYLIST) -> dict:
    """Read public embed trackList twice; fail closed without Apple Music writes."""
    target = playlist_id(playlist)
    diagnostics = dict(source='spotify_embed', playlist_found=False)
    if not _LOCK.acquire(blocking=False):
        return dict(status='source_busy', diagnostics=diagnostics)
    try:
        first = parse_embed(fetch_embed(target), target)
        diagnostics.update(parser=first['parser'], playlist_found=True, track_count=len(first['tracks']))
        time.sleep(PACE)
        try:
            second = parse_embed(fetch_embed(target), target)
        except SourceError as error:
            if str(error) == 'playlist_identity':
                return dict(status='source_unstable', diagnostics=dict(diagnostics, reason='snapshot_identity_mismatch'))
            raise
        if first['tracks'] != second['tracks']:
            return dict(status='source_unstable', diagnostics=dict(diagnostics, reason='snapshot_mismatch'))
        source = dict(service='spotify', playlist_id=target,
                      url='https://open.spotify.com/playlist/' + target, name='Discover Weekly')
        canonical = json.dumps(dict(source=source, tracks=first['tracks']), sort_keys=True,
                               separators=(',', ':'), ensure_ascii=False).encode()
        return dict(status='ok', source=source, tracks=first['tracks'],
                    extracted_at=datetime.now(timezone.utc).isoformat(),
                    fingerprint=hashlib.sha256(canonical).hexdigest(), fingerprint_version=2,
                    completeness_basis='embedded_tracklist',
                    diagnostics=dict(diagnostics, warnings=first['warnings']))
    except SourceError as error:
        return dict(status='source_incomplete', diagnostics=dict(diagnostics, reason=str(error)))
    except Exception:
        return dict(status='source_incomplete', diagnostics=dict(diagnostics, reason='reader_error'))
    finally:
        # Pace even failed requests and sequential callers; lock covers the wait.
        time.sleep(PACE)
        _LOCK.release()
