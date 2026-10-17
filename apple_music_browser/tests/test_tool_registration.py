"""Exercise real FastMCP registration with mocked upstream modules."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


class RegistrationTests(unittest.TestCase):
    def test_workflow_schemas_and_no_browser_start(self):
        directory = Path(__file__).resolve().parents[1]
        if not (directory / 'server.py').exists():
            directory = Path('/opt/addon')
        package = ModuleType('applemusic_mcp')
        package.browser = Mock()
        package.amp_api = Mock()
        auth = ModuleType('applemusic_mcp.auth')
        auth.save_user_token = Mock()
        spec = importlib.util.spec_from_file_location('tested_facade', directory / 'server.py')
        facade = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'applemusic_mcp': package, 'applemusic_mcp.auth': auth}), patch.object(sys, 'path', [str(directory)] + sys.path):
            spec.loader.exec_module(facade)
        async def check():
            tools = {tool.name: tool for tool in await facade.mcp.list_tools()}
            discovery = facade.discovery
            added = (*facade.web_ops.READ_TOOLS, *facade.web_ops.WRITE_TOOLS,
                     *facade.local_tools.READ_TOOLS, *facade.local_tools.WRITE_TOOLS)
            self.assertEqual(len(tools), 19 + len(discovery.READ_TOOLS) + len(added))
            for module in (facade.web_ops, facade.local_tools):
                for name in module.READ_TOOLS:
                    self.assertTrue(tools[name].annotations.readOnlyHint)
                for name in module.WRITE_TOOLS:
                    self.assertFalse(tools[name].annotations.readOnlyHint)
            for name in ('remove_library_song', 'delete_playlist_folder', 'set_web_rating', 'clear_catalog_cache'):
                self.assertTrue(tools[name].annotations.destructiveHint)
                self.assertFalse(tools[name].inputSchema['properties']['confirm']['default'])
            self.assertEqual(tools['set_web_rating'].inputSchema['properties']['action']['enum'], ['favorite', 'love', 'dislike', 'clear'])
            self.assertEqual(tools['add_library_catalog'].inputSchema['required'], ['catalog_ids'])
            for name in discovery.READ_TOOLS:
                self.assertIn(name, tools)
                self.assertTrue(tools[name].annotations.readOnlyHint)
                self.assertTrue(tools[name].annotations.idempotentHint)
                self.assertFalse(tools[name].annotations.destructiveHint)
            self.assertEqual(tools['catalog_identifiers'].inputSchema['required'], ['identifiers'])
            self.assertEqual(tools['resolve_isrc'].inputSchema['required'], ['isrcs'])
            self.assertEqual(tools['artist_discovery'].inputSchema['properties']['view']['enum'], ['top-songs', 'similar-artists'])
            for name, required in (
                ('search_playlist_tracks', ['playlist_id', 'term']),
                ('copy_playlist', ['playlist_id', 'name']),
                ('replace_playlist_track', ['playlist_id', 'relationship_id', 'replacement_catalog_id']),
                ('add_playlist_tracks', ['playlist_id', 'catalog_ids']),
            ):
                self.assertEqual(tools[name].inputSchema['required'], required)
            # Real SDK dispatch must reject coerced confirmation values too.
            for confirm in (1, 'true', False):
                for name, args in (
                    ('remove_library_song', {'library_song_id': 'i.fixture'}),
                    ('delete_playlist_folder', {'folder_id': 'p.fixture'}),
                    ('set_web_rating', {'catalog_id': '123', 'action': 'clear'}),
                    ('clear_catalog_cache', {}),
                ):
                    with self.assertRaises(Exception):
                        await facade.mcp.call_tool(name, dict(args, confirm=confirm))
            facade.web_ops.amp_api._headers.assert_not_called()
            self.assertEqual(facade.web_ops.amp_api.requests.mock_calls, [])
        asyncio.run(check())
        package.browser._engine.submit.assert_not_called()
