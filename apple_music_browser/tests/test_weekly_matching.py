"""Offline matching fixtures. No catalog credentials or live HTTP."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import weekly_matching as ops


def track(title='Into The Future', artists=None, duration=278095):
    return dict(title=title, artists=artists or ['Sam Skilz'], duration_ms=duration)


DETAILS = {}


def candidate(t, code='FRX452655630'):
    identifier = len(DETAILS) + 1
    item = dict(id=identifier, title=t['title'], artist={'name': t['artists'][0]},
                duration=round(t['duration_ms'] / 1000), isrc=code)
    DETAILS[identifier] = dict(item, contributors=[{'name': a} for a in t['artists']])
    return item


def response(data, status=200):
    return Mock(status_code=status, json=Mock(return_value={'data': data, 'total': len(data)}))


class WeeklyTests(unittest.TestCase):
    def setUp(self):
        ops._LAST = ops._COOLDOWN = 0
        self.p = patch.object(ops.requests, 'get')
        DETAILS.clear()
        self.http = self.p.start()
        self.get = Mock()
        self.detail = Mock(side_effect=lambda identifier: Mock(
            status_code=200, json=Mock(return_value=DETAILS[identifier])))
        def dispatch(url, **kwargs):
            if url == ops.DEEZER_SEARCH:
                return self.get(url, **kwargs)
            return self.detail(int(url.rsplit('/', 1)[1]))
        self.http.side_effect = dispatch
        self.addCleanup(self.p.stop)
        self.sleep = patch.object(ops.time, 'sleep').start()
        self.addCleanup(patch.stopall)

    def test_sam_and_30_order_duplicates(self):
        tracks = [dict(track(), id=str(i)) for i in range(29)] + [dict(track(), id='0')]
        self.get.return_value = response([candidate(track())])
        rows = ops.enrich_deezer_isrc(tracks)['tracks']
        self.assertEqual([r['source'] for r in rows], tracks)
        self.assertEqual([r['isrcs'] for r in rows], [['FRX452655630']] * 30)
        self.get.assert_called_once()
        call = self.get.call_args
        self.assertEqual(call.args, (ops.DEEZER_SEARCH,))
        self.assertEqual(call.kwargs['timeout'], (5, 10))
        self.assertFalse(call.kwargs['allow_redirects'])
        rows[0]['isrcs'].clear()
        self.assertEqual(rows[-1]['isrcs'], ['FRX452655630'])

    def test_query_fallbacks(self):
        for title, code in [('Secrets Too Soon (19:26 Remix)', 'NL8RL2503141'),
                            ('Light Explosion (Radio Edit)', 'US83Z2606265')]:
            t = track(title, ['Artist', 'Guest'])
            self.get.reset_mock()
            self.get.side_effect = [response([]), response([]), response([candidate(t, code)])]
            row = ops.enrich_deezer_isrc([t])['tracks'][0]
            self.assertEqual(row['isrcs'], [code])
            self.assertEqual([c.kwargs['params']['q'] for c in self.get.call_args_list],
                             [title + ' Artist Guest', title + ' Artist', title])

    def test_ambiguous_duration_artist_and_versions(self):
        t = track()
        self.get.return_value = response([candidate(t), candidate(t, 'NL8RL2503141')])
        self.assertEqual(ops.enrich_deezer_isrc([t])['tracks'][0]['status'], 'ambiguous')
        for changes in [dict(duration=290),
                        *[dict(title=t['title'] + suffix) for suffix in
                          [' (Radio Edit)', ' - Mixed', ' (Remix)', ' (Extended Mix)']]]:
            self.get.return_value = response([dict(candidate(t), **changes)])
            self.assertEqual(ops.enrich_deezer_isrc([t])['tracks'][0]['status'], 'missing')
        self.assertFalse(ops.compatible(track('Song (A Remix)'), 'Song (B Remix)', ['Sam Skilz'], 278000))

    def test_bounded_errors_and_truncation(self):
        for failure in [response([], 429), response([], 503), requests.Timeout('secret URL')]:
            ops._COOLDOWN = 0
            self.get.reset_mock()
            self.get.side_effect = [failure]
            rows = ops.enrich_deezer_isrc([track(), track('Other')])['tracks']
            self.assertEqual(self.get.call_count, 1)
            self.assertTrue(all(r['status'] == 'error' for r in rows))
            self.assertNotIn('secret', str(rows))
        ops._COOLDOWN = 0
        self.get.side_effect = None
        self.get.return_value = Mock(status_code=200, json=Mock(return_value={'data': [], 'total': 301, 'next': 'private'}))
        self.assertEqual(ops.enrich_deezer_isrc([track()])['tracks'][0]['reason'], 'candidate_limit')
        for bad in [[], [track()] * 31, [dict(track(), duration_ms=None)]]:
            with self.assertRaises(ValueError):
                ops.enrich_deezer_isrc(bad)

    def test_be_isrc_misses_strict_fallback_and_complete_order(self):
        tracks = [track('Let It All Blur (ASOT 1288)', ['Hel:sløwed'], 195000),
                  track('Nuru Mpya (ABGT688) - Mixed', ['Ronski Speed'], 286875)]
        codes = ['NLF712605000', 'GBEWA2603551']
        self.get.side_effect = [response([candidate(t, c)]) for t, c in zip(tracks, codes)]
        def search(path, params):
            t = next(t for t in tracks if params['term'].startswith(t['title']))
            return {'results': {'songs': {'data': [dict(id=str(tracks.index(t)+1), type='songs',
                attributes=dict(name=t['title'], artistName=t['artists'][0], durationInMillis=t['duration_ms']))]}}}
        apple = SimpleNamespace(_store=Mock(), resolve_isrc=Mock(side_effect=lambda codes, store: {'matches': {codes[0]: []}}),
                                _request=Mock(side_effect=search))
        with patch.dict(sys.modules, {'discovery': apple}):
            result = ops.check_discover_weekly(tracks + tracks[:1])
        self.assertTrue(result['complete'])
        self.assertEqual(result['desired_catalog_ids'], ['1', '2', '1'])
        self.assertEqual([r['method'] for r in result['tracks']], ['metadata'] * 3)
        self.assertEqual(apple.resolve_isrc.call_args.args[1], 'be')
        apple._request.side_effect = None
        apple._request.return_value = {'results': {'songs': {'data': []}}}
        self.get.side_effect = None
        self.get.return_value = response([])
        with patch.dict(sys.modules, {'discovery': apple}):
            self.assertIsNone(ops.check_discover_weekly(tracks)['desired_catalog_ids'])

    def test_apple_isrc_priority_ambiguity_and_failure(self):
        t = track()
        item = dict(id='123', type='songs', attributes=dict(name=t['title'],
                    artistName='Sam Skilz', durationInMillis=t['duration_ms']))
        self.get.return_value = response([candidate(t)])
        apple = SimpleNamespace(_store=Mock(), resolve_isrc=Mock(return_value={'matches': {'FRX452655630': [item]}}),
                                _request=Mock())
        with patch.dict(sys.modules, {'discovery': apple}):
            result = ops.check_discover_weekly([t])
            self.assertEqual(result['desired_catalog_ids'], ['123'])
            apple._request.assert_not_called()
            apple.resolve_isrc.return_value = {'matches': {'FRX452655630': []}}
            apple._request.return_value = {'results': {'songs': {'data': [item, dict(item, id='456')]}}}
            result = ops.check_discover_weekly([t])
            self.assertEqual(result['tracks'][0]['status'], 'ambiguous')
            self.assertIsNone(result['desired_catalog_ids'])
            apple._request.side_effect = RuntimeError('private upstream')
            result = ops.check_discover_weekly([t])
            self.assertEqual(result['tracks'][0]['status'], 'error')
            self.assertNotIn('private', str(result))

    def test_deezer_separate_version_field(self):
        self.get.return_value = response([dict(candidate(track()), title_version='(Extended Mix)')])
        self.assertEqual(ops.enrich_deezer_isrc([track()])['tracks'][0]['status'], 'missing')


    def test_full_contributors_and_duplicate_roles(self):
        fixtures = [
            ('Broken Glass (Helsloot Remix)', ['Booka Shade', 'Helsloot'], 3316562851),
            ('River In Me', ['Ruben de Ronde', 'Amber Revival'], 3748965282),
            ('Secrets Too Soon (19:26 Remix)', ['Henri Bergmann', 'Wennink', '19:26'], 3210643451),
        ]
        for title, artists, identifier in fixtures:
            with self.subTest(title=title):
                t = track(title, artists)
                item = candidate(t)
                detail = DETAILS[item['id']]
                item['id'] = detail['id'] = identifier
                detail['contributors'].append(dict(name=artists[0].upper(), role='Remixer'))
                self.get.return_value = response([item, item])
                self.detail.side_effect = None
                self.detail.return_value = Mock(status_code=200, json=Mock(return_value=detail))
                self.detail.reset_mock()
                row = ops.enrich_deezer_isrc([t])['tracks'][0]
                self.assertEqual(row['status'], 'resolved')
                self.detail.assert_called_once_with(identifier)
                call = self.http.call_args
                self.assertEqual(call.args[0], f'https://api.deezer.com/track/{identifier}')
                self.assertEqual(call.kwargs['timeout'], (5, 10))
                self.assertFalse(call.kwargs['allow_redirects'])

    def test_detail_malformed_and_inconsistent_fail_closed(self):
        t = track(artists=['A', 'B'])
        item = candidate(t)
        base = DETAILS[item['id']]
        self.get.return_value = response([item])
        for changes in [dict(id=True), dict(id=999), dict(title='Other'),
                        dict(title_version='Remix'), dict(duration=279), dict(duration=True),
                        dict(isrc='NL8RL2503141'), dict(isrc=None),
                        dict(contributors=None), dict(contributors=[]),
                        dict(contributors=[{}]), dict(contributors=[{'name': 123}])]:
            with self.subTest(changes=changes):
                self.detail.side_effect = None
                self.detail.return_value = Mock(status_code=200, json=Mock(return_value=dict(base, **changes)))
                row = ops.enrich_deezer_isrc([t])['tracks'][0]
                self.assertEqual(row['status'], 'error')
                self.assertEqual(row['isrcs'], [])
        for names in [['A'], ['A', 'B', 'C'], ['Other']]:
            self.detail.return_value = Mock(status_code=200, json=Mock(return_value=dict(
                base, contributors=[{'name': n} for n in names])))
            self.assertEqual(ops.enrich_deezer_isrc([t])['tracks'][0]['status'], 'missing')

    def test_cheap_checks_skip_details_and_detail_cooldown(self):
        t = track()
        self.get.return_value = response([dict(candidate(t), duration=300),
                                        dict(candidate(t), title='Other')])
        self.assertEqual(ops.enrich_deezer_isrc([t])['tracks'][0]['status'], 'missing')
        self.detail.assert_not_called()
        self.get.return_value = response([candidate(t)])
        self.detail.side_effect = None
        self.detail.return_value = response([], 429)
        rows = ops.enrich_deezer_isrc([t, track('Other')])['tracks']
        self.assertEqual([r['reason'] for r in rows], ['rate_limited', 'cooldown'])

    def test_apple_combined_credits(self):
        t = track(artists=['Henri Bergmann', 'Wennink', '19:26'])
        for artist in ['Henri Bergmann, Wennink & 19:26',
                       'Henri Bergmann and Wennink feat. 19:26',
                       'Henri Bergmann featuring Wennink & 19:26']:
            item = dict(id='123', type='songs', attributes=dict(
                name=t['title'], artistName=artist, durationInMillis=t['duration_ms']))
            self.assertEqual(ops._apple_candidates(t, [item]), ['123'])
            item['attributes']['artistName'] = 'Henri Bergmann & Wennink'
            self.assertEqual(ops._apple_candidates(t, [item]), [])


    def test_search_without_isrc_and_separate_detail_version(self):
        t = track('Song (Extended Mix)', ['A', 'B'])
        item = candidate(t)
        del item['isrc']
        detail = DETAILS[item['id']]
        detail.update(title='Song', title_version='(Extended Mix)')
        self.get.return_value = response([item])
        self.assertEqual(ops.enrich_deezer_isrc([t])['tracks'][0]['status'], 'resolved')

    def test_detail_transport_and_payload_failures_are_sanitized(self):
        for failure in [requests.Timeout('private URL'),
                        Mock(status_code=200, json=Mock(side_effect=ValueError('private body'))),
                        Mock(status_code=200, json=Mock(return_value=['private body'])),
                        Mock(status_code=200, json=Mock(return_value={'error': 'private body'}))]:
            ops._COOLDOWN = 0
            self.get.return_value = response([candidate(track())])
            self.detail.side_effect = [failure]
            row = ops.enrich_deezer_isrc([track()])['tracks'][0]
            self.assertEqual(row['status'], 'error')
            self.assertNotIn('private', str(row))

    def test_paginated_live_regression_metadata(self):
        for title, artist, total, code in [
                ('Try', 'Dyzen', 28, 'QMBZ92443609'),
                ('Hazy', 'Grado', 38, 'QT9XH2687244'),
                ('Believe', 'GONE', 296, 'FR9EJ2600070')]:
            for position in (0, total - 1):
                with self.subTest(title=title, position=position):
                    t = track(title, [artist])
                    match = candidate(t, code)
                    items = [dict(id=10000 + i, title='Other', duration=278)
                             for i in range(total)]
                    items[position] = match
                    def search(url, **kwargs):
                        params = kwargs['params']
                        self.assertEqual(params['q'], title + ' ' + artist)
                        self.assertEqual(params['limit'], 100)
                        offset = params['index']
                        payload = dict(data=items[offset:offset + 100], total=total)
                        if offset + 100 < total:
                            payload['next'] = 'https://untrusted.invalid/private'
                        return Mock(status_code=200, json=Mock(return_value=payload))
                    self.get.reset_mock()
                    self.detail.reset_mock()
                    self.get.side_effect = search
                    row = ops.enrich_deezer_isrc([t])['tracks'][0]
                    self.assertEqual(row['isrcs'], [code])
                    self.assertEqual([c.kwargs['params']['index'] for c in self.get.call_args_list],
                                     list(range(0, total, 100)))
                    self.detail.assert_called_once_with(match['id'])

    def test_pagination_fail_closed_and_drift(self):
        items = [dict(id=i + 1, title='Other', duration=278) for i in range(301)]
        def page(start, end, total, **extra):
            return dict(data=items[start:end], total=total, **extra)
        cases = [
            ([page(0, 100, 301)], 'candidate_limit'),
            ([page(0, 101, 101)], 'candidate_limit'),
            ([page(0, 100, 300, next='private'), page(100, 200, 300, next='private'),
              page(200, 300, 300, next='private')], 'candidate_limit'),
            ([page(0, 100, 200, next='private'), page(100, 200, 301)], 'candidate_limit'),
            ([page(0, 100, 300), page(100, 200, 300),
              page(200, 296, 300)], 'candidate_limit'),
            ([dict(data=None, total=28)], 'invalid_response'),
            ([dict(data=[], total='28')], 'invalid_response'),
            ([page(0, 0, 28, next='private')], 'invalid_response'),
            ([page(0, 28, 38)], 'invalid_response'),
            ([page(0, 28, -1)], 'invalid_response'),
            ([page(0, 28, True)], 'invalid_response'),
            ([page(0, 28, 28, next={})], 'invalid_response'),
            ([page(0, 100, 200), page(0, 100, 200)], 'invalid_response'),
            ([page(0, 100, 201), page(100, 200, 200), page(200, 200, 200)], 'candidate_limit'),
        ]
        for payloads, reason in cases:
            with self.subTest(payloads=[(len(p['data']) if isinstance(p['data'], list) else None, p['total']) for p in payloads]):
                self.get.reset_mock()
                self.get.side_effect = [Mock(status_code=200, json=Mock(return_value=p))
                                        for p in payloads]
                row = ops.enrich_deezer_isrc([track()])['tracks'][0]
                self.assertEqual(row['reason'], reason)
                self.assertEqual(row['isrcs'], [])
                self.assertNotIn('private', str(row))
                self.assertLessEqual(self.get.call_count, 3)
        # Small drift is safe if the entire highest reported range is covered.
        for totals in [(299, 300, 298), (298, 299, 300)]:
            self.get.side_effect = [Mock(status_code=200, json=Mock(return_value=
                page(i * 100, (i + 1) * 100, total))) for i, total in enumerate(totals)]
            data, error = ops._search('same query')
            self.assertIsNone(error)
            self.assertEqual(len(data), 300)

    def test_later_page_conflict_prevents_first_page_uniqueness(self):
        t = track()
        first, last = candidate(t), candidate(t, 'NL8RL2503141')
        fillers = [dict(id=10000 + i, title='Other', duration=278) for i in range(99)]
        self.get.side_effect = [
            Mock(status_code=200, json=Mock(return_value=dict(
                data=[first] + fillers, total=101, next='private'))),
            response([last])]
        self.assertEqual(ops.enrich_deezer_isrc([t])['tracks'][0]['status'], 'ambiguous')


    def test_equivalent_apple_releases_and_conflicts(self):
        from copy import deepcopy
        t = track('Believe', ['GONE'], 180000)
        code = 'FR9EJ2600070'
        item = dict(id='100', type='songs', attributes=dict(
            name='Believe', artistName='GONE', durationInMillis=180000, isrc=code))
        self.get.return_value = response([candidate(t, code)])
        apple = SimpleNamespace(_store=Mock(), resolve_isrc=Mock(), _request=Mock())
        def run(items):
            apple.resolve_isrc.return_value = {'matches': {code: items}}
            with patch.dict(sys.modules, {'discovery': apple}):
                return ops.check_discover_weekly([t, t])
        for items in ([item, dict(item, id='20')], [dict(item, id='20'), item]):
            result = run(items)
            self.assertEqual(result['desired_catalog_ids'], ['20', '20'])
            self.assertTrue(result['read_only'])
            self.assertEqual(result['tracks'][0]['reason'], 'equivalent_catalog_releases')
            self.assertEqual(result['tracks'][0]['equivalent_catalog_ids'], ['20', '100'])
        for changes in [dict(name='Believe (Remix)'), dict(name='Other'),
                        dict(isrc='NL8RL2503141'), dict(isrc=None), dict(isrc='bad'),
                        dict(artistName='GONE & Guest'), dict(durationInMillis=182001)]:
            with self.subTest(changes=changes):
                other = deepcopy(item)
                other['id'] = '20'
                other['attributes'].update(changes)
                result = run([item, other])
                self.assertEqual(result['tracks'][0]['status'], 'ambiguous')
                self.assertIsNone(result['desired_catalog_ids'])
        # Both match the source tolerance, but not each other.
        low, high = deepcopy(item), deepcopy(item)
        low['attributes']['durationInMillis'] = 178000
        high['attributes']['durationInMillis'] = 182000
        high['id'] = '20'
        self.assertEqual(run([low, high])['tracks'][0]['status'], 'ambiguous')
        self.assertEqual(run([item])['desired_catalog_ids'], ['100', '100'])
        apple._request.assert_not_called()

    def test_metadata_equivalence_requires_isrc(self):
        t = track()
        item = dict(id='100', type='songs', attributes=dict(name=t['title'],
                    artistName='Sam Skilz', durationInMillis=t['duration_ms'], isrc='FRX452655630'))
        self.get.return_value = response([candidate(t)])
        apple = SimpleNamespace(_store=Mock(), resolve_isrc=Mock(
            return_value={'matches': {'FRX452655630': []}}), _request=Mock())
        for code, expected in [('FRX452655630', 'resolved'), ('NL8RL2503141', 'ambiguous'),
                               (None, 'ambiguous')]:
            other = dict(item, id='20', attributes=dict(item['attributes'], isrc=code))
            apple._request.return_value = {'results': {'songs': {'data': [item, other]}}}
            with patch.dict(sys.modules, {'discovery': apple}):
                row = ops.check_discover_weekly([t])['tracks'][0]
            self.assertEqual(row['status'], expected)
