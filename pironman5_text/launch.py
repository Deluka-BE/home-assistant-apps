"""GPL-2.0-only. Version-checked launcher, no changes to installed source files."""
from importlib import import_module
import json
from pathlib import Path

EXPECTED = {'pironman5': '1.2.6', 'pm_auto': '1.2.5', 'pm_dashboard': '1.2.6'}
for package, expected in EXPECTED.items():
    actual = import_module(package + '.version').__version__
    if actual != expected:
        raise RuntimeError(f'Unsupported {package} {actual}; expected {expected}. Stop this test add-on and use the original.')
    print(f'{package}: {actual}', flush=True)

from text_oled import make_text_oled
from api import register
import pm_auto.pm_auto as auto_module
from pm_auto.oled import OLED
auto_module.OLED = make_text_oled(OLED)

from pironman5.pironman5 import Pironman5
import pm_dashboard.pm_dashboard as dashboard_module

config_path = Path('/data/config.json')
seed = Path('/config/initial-config.json')
if not config_path.exists() and seed.exists():
    config = json.loads(seed.read_text())
    if not isinstance(config, dict) or not isinstance(config.get('system'), dict):
        raise ValueError('initial-config.json must be the data object from get-config')
    config_path.write_text(json.dumps(config, indent=2))

service = Pironman5(config_path=str(config_path))
register(dashboard_module.__app__, service.pm_auto.oled)
service.set_debug_level('INFO')
print('OLED text extension 0.2.0 ready; API port 34001', flush=True)
service.start()
