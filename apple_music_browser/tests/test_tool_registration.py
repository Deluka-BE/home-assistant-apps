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
            self.assertEqual(len(tools), 19 + len(discovery.READ_TOOLS))
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
        asyncio.run(check())
        package.browser._engine.submit.assert_not_called()
