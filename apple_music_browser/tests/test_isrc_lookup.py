"""MusicBrainz fixtures only; no external service calls."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import requests

path = Path(__file__).resolve().parents[1] / 'isrc_lookup.py'
if not path.exists():
    path = Path('/opt/addon/isrc_lookup.py')
spec = importlib.util.spec_from_file_location('tested_isrc_lookup', path)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)
TRACK = '4uLU6hMCjMI75M1A2tKUQC'
OTHER = '0' * 22
URL = 'https://open.spotify.com/track/' + TRACK
MBID = '12345678-1234-1234-1234-123456789abc'
CODE = 'USABC2400001'


def response(data=None, status=200):
    return Mock(status_code=status, json=Mock(return_value=data))


def relation():
    return response({'resource': URL, 'relations': [
        {'target-type': 'recording', 'recording': {'id': MBID}}]})


def recording(codes=None):
    return response({'id': MBID, 'title': 'Fixture', 'length': 123000,
                     'artist-credit': [{'name': 'Artist'}], 'isrcs': [CODE] if codes is None else codes})


class LookupTests(unittest.TestCase):
    def setUp(self):
        ops._LAST_REQUEST = None
        self.get_patch = patch.object(ops.requests, 'get')
        self.get = self.get_patch.start()
        self.addCleanup(self.get_patch.stop)
        self.sleep_patch = patch.object(ops.time, 'sleep')
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    def lookup(self, tracks=None):
        return ops.lookup_spotify_isrc([TRACK] if tracks is None else tracks)['tracks']

    def test_valid_id_and_url_tracking_stripped(self):
        for value in (TRACK, URL, URL + '?si=tracking&utm_source=fixture', URL + '/#fragment'):
            self.assertEqual(ops.track_id(value), TRACK)

    def test_invalid_host_resource_and_input(self):
        values = ['https://evil.example/track/' + TRACK,
                  'https://open.spotify.com/playlist/' + TRACK,
                  'https://open.spotify.com@evil.example/track/' + TRACK,
                  'http://open.spotify.com/track/' + TRACK, 'spotify:track:' + TRACK,
                  'https://open.spotify.com:443/track/' + TRACK, 'bad', None,
                  '\n' + URL]
        for row in self.lookup(values):
            self.assertEqual((row['status'], row['reason']), ('error', 'invalid_input'))
            self.assertIsNone(row['spotify_id'])
        self.get.assert_not_called()

    def test_resolved_and_request_etiquette(self):
        self.get.side_effect = [relation(), recording()]
        row = self.lookup()[0]
        self.assertEqual(row['status'], 'resolved')
        self.assertEqual(row['isrcs'], [CODE])
        self.assertEqual(row['canonical_url'], URL)
        self.assertEqual(row['recordings'][0]['duration_ms'], 123000)
        self.assertEqual(row['recordings'][0]['artist_credit'], [{'name': 'Artist'}])
        calls = self.get.call_args_list
        self.assertEqual(calls[0].args, (ops.API + '/url',))
        self.assertEqual(calls[0].kwargs['params'], dict(resource=URL, inc='recording-rels', fmt='json'))
        self.assertEqual(calls[1].args, (ops.API + '/recording/' + MBID,))
        for call in calls:
            self.assertEqual(call.kwargs['timeout'], (5, 15))
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertIn('home-assistant-apps', call.kwargs['headers']['User-Agent'])
        self.sleep.assert_called_once()

    def test_missing_url_relation_and_isrc(self):
        for fixtures, reason in (([response(status=404)], 'url_not_found'),
                                 ([response({'resource': URL, 'relations': []})], 'no_recording_relation'),
                                 ([relation(), recording([])], 'no_isrc')):
            self.get.side_effect = fixtures
            row = self.lookup()[0]
            self.assertEqual((row['status'], row['reason'], row['isrcs']), ('missing', reason, []))

    def test_multiple_isrcs_ambiguous(self):
        self.get.side_effect = [relation(), recording([CODE, 'GBABC2400002', CODE])]
        row = self.lookup()[0]
        self.assertEqual(row['status'], 'ambiguous')
        self.assertEqual(row['isrcs'], ['GBABC2400002', CODE])

    def test_conflicting_recordings(self):
        second = 'abcdefab-1234-1234-1234-123456789abc'
        payload = relation().json()
        payload['relations'].append({'target-type': 'recording', 'recording': {'id': second}})
        record = recording(['GBABC2400002']).json()
        record['id'] = second
        self.get.side_effect = [response(payload), recording(), response(record)]
        row = self.lookup()[0]
        self.assertEqual(row['status'], 'ambiguous')
        self.assertEqual(len(row['recordings']), 2)

    def test_order_and_repeated_lookup_deduplication(self):
        self.get.side_effect = [relation(), recording(), response(status=404)]
        values = [TRACK, OTHER, URL + '?si=tracking', TRACK]
        rows = self.lookup(values)
        self.assertEqual([row['input'] for row in rows], values)
        self.assertEqual([row['status'] for row in rows], ['resolved', 'missing', 'resolved', 'resolved'])
        self.assertEqual(self.get.call_count, 3)
        rows[0]['isrcs'].clear()
        self.assertEqual(rows[2]['isrcs'], [CODE])

    def test_errors_are_explicit_sanitized_and_deduplicated(self):
        for failure in (requests.Timeout('private'), requests.ConnectionError('private'),
                        response(status=429), response(status=503), response(status=302),
                        response([]), response({'error': 'private'}),
                        response({'resource': 'wrong', 'relations': []}),
                        response({'resource': URL, 'relations': None})):
            self.get.reset_mock()
            self.get.side_effect = [failure]
            rows = self.lookup([TRACK, TRACK])
            self.assertEqual(self.get.call_count, 1)
            for row in rows:
                self.assertEqual((row['status'], row['reason'], row['isrcs']), ('error', 'musicbrainz_error', []))
                self.assertNotIn('private', str(row))
        self.get.side_effect = [relation(), requests.Timeout()]
        self.assertEqual(self.lookup()[0]['status'], 'error')
        self.get.side_effect = [relation(), recording(['invalid'])]
        self.assertEqual(self.lookup()[0]['status'], 'error')
        malformed = response()
        malformed.json.side_effect = ValueError('private')
        self.get.side_effect = [malformed]
        self.assertEqual(self.lookup()[0]['status'], 'error')

    def test_bound_before_network(self):
        for values in ([], [TRACK] * 31, TRACK):
            with self.assertRaises(ValueError):
                ops.lookup_spotify_isrc(values)
        self.get.assert_not_called()
        self.get.side_effect = [response(status=404)]
        self.assertEqual(len(self.lookup([TRACK] * 30)), 30)
        self.get.assert_called_once()

    def test_shared_recording_deduplicated_across_tracks(self):
        other_relation = relation().json()
        other_relation['resource'] = 'https://open.spotify.com/track/' + OTHER
        self.get.side_effect = [relation(), recording(), response(other_relation)]
        rows = self.lookup([TRACK, OTHER])
        self.assertEqual([row['status'] for row in rows], ['resolved', 'resolved'])
        self.assertEqual(self.get.call_count, 3)

    def test_partial_recording_failure_never_resolves(self):
        payload = relation().json()
        payload['relations'].append({'target-type': 'recording', 'recording': {
            'id': 'abcdefab-1234-1234-1234-123456789abc'}})
        self.get.side_effect = [response(payload), recording(), response(status=503)]
        row = self.lookup()[0]
        self.assertEqual(row['status'], 'error')
        self.assertEqual(row['isrcs'], [])

    def test_rate_limit_spacing(self):
        ops._LAST_REQUEST = 10.0
        self.get.return_value = response(status=404)
        with patch.object(ops.time, 'monotonic', side_effect=[10.2, 11.05]):
            self.lookup()
        self.assertAlmostEqual(self.sleep.call_args.args[0], .85)
