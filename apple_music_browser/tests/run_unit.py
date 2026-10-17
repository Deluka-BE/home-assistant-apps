"""Dependency preflight and complete unit discovery, also used inside release CI."""
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
import sys
import unittest

for package, expected in (("applemusic-mcp", "0.20.3"), ("mcp", "1.30.0")):
    actual = version(package)
    if actual != expected:
        raise RuntimeError(f"Expected {package} {expected}, got {actual}")
    print(f"Dependency: {package}=={actual}", flush=True)
for module in ("starlette", "uvicorn", "playwright.sync_api", "applemusic_mcp.amp_api", "applemusic_mcp.browser"):
    import_module(module)
    print(f"Import: {module} OK", flush=True)

suite = unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent), pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
