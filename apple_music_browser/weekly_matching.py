"""Bounded read-only catalog enrichment and ordered Discover Weekly checking."""
from copy import deepcopy
import re
import threading
import time
import unicodedata

import requests

DEEZER_SEARCH = 'https://api.deezer.com/search'
_LOCK = threading.Lock()
_LAST = 0.0
_COOLDOWN = 0.0
LIMIT = 25
DEEZER_PAGE_SIZE = 100
DEEZER_CANDIDATE_LIMIT = 300


def normalized(value):
    # Keep every word, including mix names, episode numbers and version qualifiers.
    return ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', value).casefold()))


def credits(values):
    return {normalized(part) for value in values
            for part in re.split(r'\s+(?:&|feat\.?|featuring|and)\s+|,\s*', value)
            if normalized(part)}


def source(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid source metadata')
    title, artists, duration = (value.get(k) for k in ('title', 'artists', 'duration_ms'))
    if (not isinstance(title, str) or not normalized(title) or len(title) > 300
            or not isinstance(artists, list) or not 1 <= len(artists) <= 20
            or any(not isinstance(a, str) or not normalized(a) or len(a) > 200 for a in artists)
            or type(duration) is not int or not 1 <= duration <= 3600000):
        raise ValueError('Title, artists and duration_ms are required')
    return title, artists, duration


def compatible(track, title, artists, duration):
    wanted, names, length = source(track)
    return (isinstance(title, str) and normalized(wanted) == normalized(title)
            and credits(names) == credits(artists)
            and type(duration) is int and abs(length - duration) <= 2000)


def _request(url, params=None):
    global _LAST, _COOLDOWN
    with _LOCK:
        if time.monotonic() < _COOLDOWN:
            return None, 'cooldown'
        time.sleep(max(0, .25 - (time.monotonic() - _LAST)))
        _LAST = time.monotonic()
        try:
            response = requests.get(url, params=params,
                                    timeout=(5, 10), allow_redirects=False)
            if response.status_code != 200:
                if response.status_code == 429 or response.status_code >= 500:
                    _COOLDOWN = time.monotonic() + 30
                return None, 'rate_limited' if response.status_code == 429 else 'upstream_error'
            payload = response.json()
            if not isinstance(payload, dict) or 'error' in payload:
                return None, 'invalid_response'
            return payload, None
        except (requests.RequestException, ValueError):
            _COOLDOWN = time.monotonic() + 30
            return None, 'connection_failed'


def _search(query):
    data, seen, high_water = [], set(), 0
    for index in range(0, DEEZER_CANDIDATE_LIMIT, DEEZER_PAGE_SIZE):
        payload, error = _request(DEEZER_SEARCH, {
            'q': query, 'limit': DEEZER_PAGE_SIZE, 'index': index})
        if error:
            return None, error
        page, total, continuation = (payload.get(k) for k in ('data', 'total', 'next'))
        if (not isinstance(page, list) or type(total) is not int or total < 0
                or (continuation is not None and
                    (not isinstance(continuation, str) or not continuation))):
            return None, 'invalid_response'
        high_water = max(high_water, total)
        if (high_water > DEEZER_CANDIDATE_LIMIT or len(page) > DEEZER_PAGE_SIZE
                or index + len(page) > DEEZER_CANDIDATE_LIMIT):
            return None, 'candidate_limit'
        # Overlapping pages can hide candidates when the upstream ordering moves.
        page_ids = set()
        for item in page:
            if (not isinstance(item, dict) or type(item.get('id')) is not int
                    or item['id'] <= 0 or item['id'] in seen):
                return None, 'invalid_response'
            page_ids.add(item['id'])
        seen.update(page_ids)
        data.extend(page)
        if not continuation and len(data) >= high_water:
            return data, None
        if index + DEEZER_PAGE_SIZE == DEEZER_CANDIDATE_LIMIT:
            return None, 'candidate_limit'
        if len(page) != DEEZER_PAGE_SIZE:
            return None, 'invalid_response'
    return None, 'candidate_limit'


def _title(item):
    title, version = item.get('title'), item.get('title_version')
    if not isinstance(title, str) or not normalized(title):
        return None
    if version is not None and not isinstance(version, str):
        return None
    if version and (' ' + normalized(version) + ' ') not in (' ' + normalized(title) + ' '):
        title += ' ' + version
    return title


def _isrc(code):
    return isinstance(code, str) and re.fullmatch(r'[A-Z]{2}[A-Z0-9]{3}[0-9]{7}', code)


def _enrich(track):
    title, artists, length = source(track)
    queries = list(dict.fromkeys([title + ' ' + ' '.join(artists), title + ' ' + artists[0], title]))
    for attempt, query in enumerate(queries, 1):
        data, error = _search(query)
        if error:
            return dict(status='error', reason=error, isrcs=[], attempts=attempt)
        if not data:
            continue
        # The complete search is bounded; duplicate IDs share a detail request.
        codes, details = set(), {}
        for item in data:
            if not isinstance(item, dict):
                return dict(status='error', reason='invalid_response', isrcs=[], attempts=attempt)
            candidate_title, duration = _title(item), item.get('duration')
            if (candidate_title is None or normalized(candidate_title) != normalized(title)
                    or type(duration) is not int or abs(length - duration * 1000) > 2000):
                continue
            identifier = item.get('id')
            if type(identifier) is not int or identifier <= 0:
                return dict(status='error', reason='invalid_response', isrcs=[], attempts=attempt)
            if identifier not in details:
                details[identifier] = _request(f'https://api.deezer.com/track/{identifier}')
            detail, error = details[identifier]
            if error:
                return dict(status='error', reason=error, isrcs=[], attempts=attempt)
            names = detail.get('contributors')
            detail_title = _title(detail)
            code = detail.get('isrc')
            if (type(detail.get('id')) is not int or detail['id'] != identifier
                    or detail_title is None or normalized(detail_title) != normalized(candidate_title)
                    or type(detail.get('duration')) is not int or detail['duration'] != duration
                    or not _isrc(code)
                    or ('isrc' in item and item['isrc'] != code)
                    or not isinstance(names, list) or not names
                    or any(not isinstance(n, dict) or not isinstance(n.get('name'), str)
                           or not normalized(n['name']) for n in names)):
                return dict(status='error', reason='invalid_response', isrcs=[], attempts=attempt)
            if compatible(track, detail_title, [n['name'] for n in names], detail['duration'] * 1000):
                codes.add(code)
        return dict(status='resolved' if len(codes) == 1 else 'ambiguous' if codes else 'missing',
                    reason='validated_metadata' if len(codes) == 1 else 'multiple_isrcs' if codes else 'metadata_mismatch',
                    isrcs=sorted(codes), attempts=attempt)
    return dict(status='missing', reason='no_results', isrcs=[], attempts=len(queries))


def enrich_deezer_isrc(tracks: list[dict]) -> dict:
    """Validate 1..30 sources against public Deezer search and details; no credentials or writes."""
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= 30:
        raise ValueError('Supply 1..30 source tracks')
    for track in tracks:
        source(track)
    rows, cache = [], {}
    for track in tracks:
        title, artists, duration = source(track)
        key = (title, tuple(artists), duration)
        if key not in cache:
            cache[key] = _enrich(track)
        rows.append(dict(source=deepcopy(track), **deepcopy(cache[key])))
    return dict(provider='deezer', read_only=True, tracks=rows)


def _apple_candidates(track, candidates):
    matches = {}
    for item in candidates:
        if not isinstance(item, dict):
            raise ValueError('Invalid Apple candidate')
        attrs = item.get('attributes', {})
        identifier = item.get('id', '')
        if (item.get('type') != 'songs' or not isinstance(identifier, str)
                or not re.fullmatch(r'[0-9]{1,30}', identifier) or not isinstance(attrs, dict)):
            raise ValueError('Invalid Apple candidate')
        artist = attrs.get('artistName')
        if isinstance(artist, str) and compatible(track, attrs.get('name'), [artist], attrs.get('durationInMillis')):
            matches[identifier] = True
    return list(matches)


def _equivalent_apple_ids(track, candidates, expected_isrc=None):
    """Collapse only a fully validated group; duration tolerance is pairwise."""
    ids = _apple_candidates(track, candidates)
    if len(ids) < 2 or len(ids) != len({item['id'] for item in candidates}):
        return None
    attrs = [item['attributes'] for item in candidates]
    codes = {a.get('isrc') for a in attrs if isinstance(a.get('isrc'), str)}
    if (len(codes) != 1 or any(not _isrc(a.get('isrc')) for a in attrs)
            or (expected_isrc is not None and codes != {expected_isrc})
            or len({normalized(a['name']) for a in attrs}) != 1
            or any(credits([a['artistName']]) != credits([attrs[0]['artistName']]) for a in attrs)
            or max(a['durationInMillis'] for a in attrs)
               - min(a['durationInMillis'] for a in attrs) > 2000):
        return None
    return sorted(ids, key=lambda identifier: (int(identifier), identifier))


def check_discover_weekly(tracks: list[dict], storefront: str = 'be') -> dict:
    """Build an ordered read-only matching report. No playlist mutations; incomplete results cannot sync."""
    import discovery
    discovery._store(storefront)
    rows = enrich_deezer_isrc(tracks)['tracks']
    for row in rows:
        track = row['source']
        row['enrichment_status'] = row['status']
        row['catalog_id'] = None
        try:
            candidates = []
            equivalent_ids = None
            if row['status'] == 'resolved':
                result = discovery.resolve_isrc(row['isrcs'], storefront)
                raw = result['matches'][row['isrcs'][0]]
                candidates = _apple_candidates(track, raw)
                equivalent_ids = _equivalent_apple_ids(track, raw, row['isrcs'][0])
                if equivalent_ids:
                    candidates = equivalent_ids[:1]
                elif len({item['id'] for item in raw}) > 1:
                    row.update(status='ambiguous', reason='multiple_catalog_ids', method='isrc')
                    continue
            row['method'] = 'isrc'
            if len(candidates) != 1:
                row['method'] = 'metadata'
                payload = discovery._request(f'/catalog/{storefront}/search',
                    {'term': track['title'] + ' ' + ' '.join(track['artists']), 'types': 'songs', 'limit': LIMIT})
                songs = payload.get('results', {}).get('songs')
                if not isinstance(songs, dict) or not isinstance(songs.get('data'), list):
                    raise ValueError('Invalid Apple search')
                if songs.get('next') or len(songs['data']) >= LIMIT:
                    row.update(status='ambiguous', reason='candidate_limit')
                    continue
                candidates = _apple_candidates(track, songs['data'])
                equivalent_ids = _equivalent_apple_ids(track, songs['data'])
                if equivalent_ids:
                    candidates = equivalent_ids[:1]
            row.update(status='resolved' if len(candidates) == 1 else 'ambiguous' if candidates else 'missing',
                       reason='validated_metadata' if len(candidates) == 1 else 'multiple_catalog_ids' if candidates else 'no_strict_match',
                       catalog_id=candidates[0] if len(candidates) == 1 else None)
            if equivalent_ids:
                row.update(reason='equivalent_catalog_releases', equivalent_catalog_ids=equivalent_ids)
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError):
            row.update(status='error', reason='apple_lookup_failed')
    complete = all(row['status'] == 'resolved' for row in rows)
    return dict(read_only=True, storefront=storefront, complete=complete, tracks=rows,
                desired_catalog_ids=[r['catalog_id'] for r in rows] if complete else None)
