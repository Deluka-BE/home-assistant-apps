"""Every new tool runs against fixture HTTP responses, never an Apple account."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

api = Mock(API="api", TIMEOUT=30)
package = ModuleType("applemusic_mcp")
package.amp_api = api
path = Path(__file__).resolve().parents[1] / "discovery.py"
if not path.exists():
    path = Path("/opt/addon/discovery.py")
spec = importlib.util.spec_from_file_location("tested_discovery", path)
ops = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"applemusic_mcp": package}):
    spec.loader.exec_module(ops)


def resource(identifier="123", kind="songs", **attrs):
    return {"id": identifier, "type": kind, "attributes": attrs}


def response(payload=None, status=200):
    return Mock(status_code=status, json=Mock(return_value=payload if payload is not None else {"data": []}))


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        api.reset_mock(return_value=True, side_effect=True)
        api.API = "api"
        api.TIMEOUT = 30
        api.throttled_recently.return_value = False
        api._headers.return_value = {"Authorization": "Bearer fixture", "Music-User-Token": "fixture-user", "Origin": "https://music.apple.com"}
        api.requests.get.return_value = response()

    def test_all_page_tools_routes_and_preserved_resources(self):
        cases = [
            ("album_tracks", {"album_id": "123", "storefront": "be"}, "/catalog/be/albums/123/tracks"),
            ("artist_discovery", {"artist_id": "123"}, "/catalog/us/artists/123/view/top-songs"),
            ("artist_discovery", {"artist_id": "123", "view": "similar-artists"}, "/catalog/us/artists/123/view/similar-artists"),
            ("catalog_genres", {}, "/catalog/us/genres"),
            ("browse_library", {}, "/me/library/songs"),
            ("browse_library", {"content_type": "albums"}, "/me/library/albums"),
            ("browse_library", {"content_type": "artists"}, "/me/library/artists"),
            ("browse_library", {"content_type": "music-videos"}, "/me/library/music-videos"),
            ("library_history", {}, "/me/library/recently-added"),
            ("library_history", {"kind": "recently-played"}, "/me/recent/played/tracks"),
            ("music_recommendations", {}, "/me/recommendations"),
            ("music_recommendations", {"kind": "heavy-rotation"}, "/me/history/heavy-rotation"),
            ("account_storefront", {}, "/me/storefront"),
        ]
        for name, args, route in cases:
            with self.subTest(name=name, args=args):
                item = resource("i.library", "library-songs", playParams={"catalogId": "123"})
                item["relationships"] = {"contents": {"data": [resource()]}}
                api.requests.get.return_value = response({"data": [item]})
                self.assertEqual(getattr(ops, name)(**args), {"data": [item], "next": None})
                call = api.requests.get.call_args
                self.assertEqual(call.args, (ops.API + route,))
                self.assertEqual(call.kwargs["headers"], api._headers.return_value)
                self.assertEqual(call.kwargs["timeout"], 30)
                self.assertFalse(call.kwargs["allow_redirects"])
        api.requests.post.assert_not_called()
        api.requests.put.assert_not_called()
        api.requests.delete.assert_not_called()

    def test_catalog_details_types_and_missing_identifiers(self):
        for kind in ("songs", "albums", "artists"):
            api.requests.get.return_value = response({"data": [resource("123", kind, isrc="USABC1234567", durationInMillis=1000)]})
            result = ops.catalog_identifiers(["123", "456", "123"], kind, "be")
            self.assertEqual(result["missing"], ["456"])
            self.assertEqual(api.requests.get.call_args.kwargs["params"], {"ids": "123,456"})
            self.assertEqual(result["data"][0]["attributes"]["durationInMillis"], 1000)

    def test_isrc_ambiguity_dedup_and_normalization(self):
        candidates = [resource("123", isrc="USABC1234567"), resource("456", isrc="USABC1234567")]
        api.requests.get.return_value = response({"data": candidates + candidates[:1]})
        result = ops.resolve_isrc(["us-abc-12-34567", "USABC1234567", "BEABC1234567"], "be")
        self.assertEqual(result["matches"]["USABC1234567"], candidates)
        self.assertEqual(result["ambiguous"], ["USABC1234567"])
        self.assertEqual(result["missing"], ["BEABC1234567"])
        self.assertEqual(api.requests.get.call_args.kwargs["params"], {"filter[isrc]": "USABC1234567,BEABC1234567"})

    def test_isrc_batches_25_and_failure_is_not_missing(self):
        codes = [f"USABC12{i:05d}" for i in range(26)]
        api.requests.get.side_effect = [response(), response(status=429)]
        with self.assertRaisesRegex(RuntimeError, "429"):
            ops.resolve_isrc(codes)
        self.assertEqual(api.requests.get.call_count, 2)
        self.assertEqual(len(api.requests.get.call_args_list[0].kwargs["params"]["filter[isrc]"].split(",")), 25)

    def test_isrc_later_pages_keep_all_candidates(self):
        api.requests.get.side_effect = [response({"data": [resource("1", isrc="USABC1234567")], "next": "/v1/catalog/us/songs?offset=1"}),
                                        response({"data": [resource("2", isrc="USABC1234567")]})]
        self.assertEqual(ops.resolve_isrc(["USABC1234567"])["ambiguous"], ["USABC1234567"])
        self.assertEqual(api.requests.get.call_args.kwargs["params"], {"filter[isrc]": "USABC1234567", "offset": 1})

    def test_suggestions_and_charts(self):
        suggestions = [{"kind": "terms", "searchTerm": "test"}]
        api.requests.get.return_value = response({"results": {"suggestions": suggestions}})
        self.assertEqual(ops.catalog_suggestions("test", "be"), {"suggestions": suggestions})
        self.assertEqual(api.requests.get.call_args.args, (ops.API + "/catalog/be/search/suggestions",))
        for kind in ("songs", "albums", "music-videos"):
            charts = {kind: [{"name": "Top", "data": [resource()], "next": "/v1/catalog/us/charts?offset=25"}]}
            api.requests.get.return_value = response({"results": charts})
            result = ops.music_charts(kind)
            self.assertTrue(result["first_page_only"])
            self.assertEqual(result["results"], charts)

    def test_session_diagnostics_are_sanitized(self):
        self.assertTrue(ops.session_diagnostics()["ok"])
        api.requests.get.side_effect = RuntimeError("credential fixture-secret at private-path")
        result = ops.session_diagnostics()
        self.assertFalse(result["ok"])
        self.assertNotIn("fixture-secret", str(result))
        self.assertNotIn("private-path", str(result))
        api.requests.get.side_effect = None
        api.throttled_recently.return_value = True
        api.requests.get.reset_mock()
        self.assertIn("rate limited", ops.session_diagnostics()["error"])
        api.requests.get.assert_not_called()

    def test_precise_http_errors_no_body_or_redirect_leaks(self):
        for status in (301, 302, 401, 403, 404, 429, 500):
            api.requests.get.return_value = response({"secret": "fixture-secret"}, status)
            with self.assertRaisesRegex(RuntimeError, str(status)) as caught:
                ops.browse_library()
            self.assertNotIn("fixture-secret", str(caught.exception))
            api.note_status.assert_called_with(status, rail="api")
        api._err.assert_not_called()

    def test_pagination_same_route_and_preserves_filters(self):
        nxt = "/v1/me/library/songs?offset=25&limit=25"
        api.requests.get.return_value = response({"data": [], "next": nxt})
        self.assertEqual(ops.browse_library()["next"], nxt)
        api.requests.get.return_value = response()
        ops.browse_library(cursor=nxt)
        self.assertEqual(api.requests.get.call_args.kwargs["params"], {"limit": 25, "offset": 25})
        for cursor in ("https://evil.invalid/v1/me/library/songs?offset=25",
                       "https://api.music.apple.com@evil.invalid/v1/me/library/songs?offset=25",
                       "/v1/me/library/albums?offset=25", "/v1/me/library/songs?offset=25&limit=1",
                       "/v1/me/library/songs?offset=1&offset=2", "/v1/me/library/songs?token=fixture",
                       "/v1/me/library/songs?offset=-1", "/v1/me/library/songs?offset=25#secret"):
            with self.subTest(cursor=cursor):
                api.requests.get.reset_mock()
                with self.assertRaises(ValueError):
                    ops.browse_library(cursor=cursor)
                api.requests.get.assert_not_called()
        for nxt in ("/v1/me/library/songs?offset=0", "https://evil.invalid/", 12, ""):
            api.requests.get.return_value = response({"data": [], "next": nxt})
            with self.assertRaises((ValueError, RuntimeError)):
                ops.browse_library()

    def test_malformed_payloads_and_unexpected_matches(self):
        for payload in ([], {}, {"data": None}, {"data": [None]}, {"data": [{}]},
                        {"data": [], "errors": [{"detail": "fixture-secret"}]}):
            api.requests.get.return_value = response(payload)
            with self.assertRaises(RuntimeError):
                ops.catalog_genres()
        api.requests.get.return_value = Mock(status_code=200, json=Mock(side_effect=ValueError("secret")))
        with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
            ops.catalog_genres()
        api.requests.get.return_value = response({"data": [resource("456", isrc="BEABC1234567")]})
        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            ops.resolve_isrc(["USABC1234567"])
        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            ops.catalog_identifiers(["123"])
        api.requests.get.return_value = response({"results": {}})
        for call in (lambda: ops.catalog_suggestions("test"), ops.music_charts):
            with self.assertRaises(RuntimeError):
                call()

    def test_invalid_inputs_never_send_requests(self):
        calls = [lambda: ops.catalog_identifiers([]), lambda: ops.catalog_identifiers(["1"] * 101),
                 lambda: ops.catalog_identifiers(["i.library"]), lambda: ops.catalog_identifiers(["１２３"]),
                 lambda: ops.catalog_identifiers(["123"], "playlists"), lambda: ops.resolve_isrc([]),
                 lambda: ops.resolve_isrc(["USABC1234567!"]), lambda: ops.resolve_isrc([None]),
                 lambda: ops.album_tracks("../songs"), lambda: ops.album_tracks("1", "../us"),
                 lambda: ops.album_tracks("1", limit=26), lambda: ops.catalog_genres(limit=True),
                 lambda: ops.artist_discovery("1", "unknown"), lambda: ops.browse_library("unknown"),
                 lambda: ops.library_history("unknown"), lambda: ops.music_recommendations("unknown"),
                 lambda: ops.music_recommendations(limit=11), lambda: ops.music_charts("artists"),
                 lambda: ops.catalog_suggestions(" "), lambda: ops.catalog_suggestions("x" * 201)]
        for call in calls:
            with self.assertRaises(ValueError):
                call()
        api.requests.get.assert_not_called()

    def test_batch_pagination_failure_and_bound(self):
        api.requests.get.side_effect = [response({"data": [resource()], "next": "/v1/catalog/us/songs?offset=1"}), response(status=500)]
        with self.assertRaisesRegex(RuntimeError, "500"):
            ops.catalog_identifiers(["123"])
        with patch.object(ops, "_page", return_value={"data": [], "next": "fixture"}) as page:
            with self.assertRaisesRegex(RuntimeError, "100 pages"):
                ops.catalog_identifiers(["123"])
            self.assertEqual(page.call_count, 100)
