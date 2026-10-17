"""Prevent publishing an image whose HA metadata advertises a different version."""
import json
from pathlib import Path
import re
import unittest


class ReleaseTests(unittest.TestCase):
    def test_release_versions_agree(self):
        directory = Path(__file__).resolve().parents[1]
        if not (directory / "release.json").exists():
            directory = Path("/opt/addon")
        release = json.loads((directory / "release.json").read_text())["version"]
        self.assertRegex(release, r"^[0-9]+\.[0-9]+\.[0-9]+$")
        self.assertEqual(re.search(r'^version: "([^"]+)"', (directory / "config.yaml").read_text(), re.M)[1], release)
        self.assertIn('io.hass.version="' + release + '"', (directory / "Dockerfile").read_text())
