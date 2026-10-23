"""Read-only Spotify URL relationship enrichment using public MusicBrainz data."""
from copy import deepcopy
import re
import threading
import time
from urllib.parse import urlsplit

import requests

API = 'https://musicbrainz.org/ws/2'
USER_AGENT = ('home-assistant-apps-apple-music-browser-isrc/1.0 '
              '(https://github.com/Deluka-BE/home-assistant-apps)')
_ID = re.compile(r'[A-Za-z0-9]{22}')
_MBID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
_ISRC = re.compile(r'[A-Z]{2}[A-Z0-9]{3}[0-9]{7}')
_LOCK = threading.Lock()
_LAST_REQUEST = None


def track_id(value):
    """Accept a bare ID or HTTPS track URL; discard query and fragment."""
    if not isinstance(value, str) or len(value) > 4096 or any(c.isspace() for c in value):
        raise ValueError('Invalid Spotify track ID/URL')
    if _ID.fullmatch(value):
        return value
    url = urlsplit(value)
    match = re.fullmatch(r'/track/([A-Za-z0-9]{22})/?', url.path)
    if url.scheme == 'https' and url.netloc == 'open.spotify.com' and match:
        return match[1]
    raise ValueError('Supply a Spotify track ID or https://open.spotify.com/track/ID')


def _request(path, params, missing_ok=False):
    global _LAST_REQUEST
    # Serialize all calls, including across concurrent MCP invocations. No retries.
    with _LOCK:
        if _LAST_REQUEST is not None:
            time.sleep(max(0, 1.05 - (time.monotonic() - _LAST_REQUEST)))
        _LAST_REQUEST = time.monotonic()
        response = requests.get(API + path, params=dict(params, fmt='json'),
                                headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'},
                                timeout=(5, 15), allow_redirects=False)
    if response.status_code == 404 and missing_ok:
        return None
    if response.status_code != 200:
        raise ValueError('MusicBrainz HTTP error')
    payload = response.json()
    if not isinstance(payload, dict) or 'error' in payload:
        raise ValueError('Invalid MusicBrainz response')
    return payload


def _lookup(url, recordings):
    data = _request('/url', {'resource': url, 'inc': 'recording-rels'}, missing_ok=True)
    if data is None:
        return dict(status='missing', reason='url_not_found', isrcs=[], recordings=[])
    if data.get('resource') != url or not isinstance(data.get('relations'), list):
        raise ValueError('Invalid URL relationship response')
    ids = []
    for relation in data['relations']:
        if relation.get('target-type') != 'recording':
            continue
        identifier = relation.get('recording', {}).get('id', '')
        if not isinstance(identifier, str) or not _MBID.fullmatch(identifier):
            raise ValueError('Invalid recording ID')
        if identifier not in ids:
            ids.append(identifier)
    if len(ids) > 100:
        raise ValueError('Too many linked recordings')
    candidates = []
    for identifier in ids:
        if identifier not in recordings:
            try:
                record = _request('/recording/' + identifier, {'inc': 'isrcs+artist-credits'})
                if record.get('id') != identifier or not isinstance(record.get('isrcs'), list):
                    raise ValueError('Invalid recording response')
                codes = record['isrcs']
                if any(not isinstance(code, str) or not _ISRC.fullmatch(code) for code in codes):
                    raise ValueError('Invalid ISRC response')
                recordings[identifier] = dict(
                    musicbrainz_id=identifier, title=record.get('title'),
                    duration_ms=record.get('length'), disambiguation=record.get('disambiguation'),
                    artist_credit=record.get('artist-credit', []), isrcs=sorted(set(codes)))
            except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
                recordings[identifier] = None
        if recordings[identifier] is None:
            raise ValueError('Recording lookup failed')
        candidates.append(recordings[identifier])
    codes = sorted({code for record in candidates for code in record['isrcs']})
    return dict(status='ambiguous' if len(codes) > 1 else 'resolved' if codes else 'missing',
                isrcs=codes, recordings=candidates,
                reason='multiple_isrcs' if len(codes) > 1 else 'exact_url_relationship' if codes
                else 'no_recording_relation' if not ids else 'no_isrc')


def lookup_spotify_isrc(tracks: list[str]) -> dict:
    """Enrich 1..30 Spotify track IDs/URLs via exact MusicBrainz relationships.

    Preserves input order and duplicates. Multiple ISRCs require caller validation;
    resolved means one reported ISRC, not a verified Apple Music match. No writes.
    """
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= 30:
        raise ValueError('Supply 1..30 Spotify tracks')
    results, cache, recordings = [], {}, {}
    for value in tracks:
        row = dict(input=value, spotify_id=None, canonical_url=None, isrcs=[], recordings=[])
        try:
            identifier = track_id(value)
        except ValueError:
            results.append(dict(row, status='error', reason='invalid_input'))
            continue
        url = 'https://open.spotify.com/track/' + identifier
        row.update(spotify_id=identifier, canonical_url=url)
        if identifier not in cache:
            try:
                cache[identifier] = _lookup(url, recordings)
            except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
                cache[identifier] = dict(status='error', reason='musicbrainz_error', isrcs=[], recordings=[])
        results.append(dict(row, **deepcopy(cache[identifier])))
    return dict(provider='musicbrainz', tracks=results)
