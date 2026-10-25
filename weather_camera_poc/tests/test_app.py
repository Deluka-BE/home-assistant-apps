import builtins
import io
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Analysis, HA, MAX_BYTES, create_app
from models import normalize


@pytest.fixture
def snapshot():
    stream = io.BytesIO()
    Image.new('RGB', (8, 8)).save(stream, format='PNG')
    return stream.getvalue()


def execute(engine, bypass=False):
    assert engine.start(bypass)
    deadline = time.monotonic() + 3
    while engine.status()['busy'] and time.monotonic() < deadline:
        time.sleep(.005)
    assert not engine.status()['busy']
    return engine.status()


def make_engine(snapshot, **options):
    ha = Mock()
    ha.daylight.return_value = True
    ha.snapshot.return_value = snapshot
    runner = Mock(return_value=dict(probabilities={'partly cloudy': .8, 'clear': .2},
                                   inference_seconds=.2, rss_before_bytes=10,
                                   rss_after_bytes=20, load_and_inference_seconds=.4))
    # Each real worker returns a fresh dictionary.
    runner.side_effect = lambda *args: dict(probabilities={'partly cloudy': .8, 'clear': .2},
                                          inference_seconds=.2, rss_before_bytes=10,
                                          rss_after_bytes=20, load_and_inference_seconds=.4)
    return Analysis(options, ha, runner)


def test_same_snapshot_no_writes_and_result_shape(snapshot):
    engine = make_engine(snapshot)
    with patch.object(builtins, 'open', side_effect=AssertionError('Filesystem access')):
        state = execute(engine)
    engine.ha.snapshot.assert_called_once_with('camera.woonkamer_vloeiend')
    assert [c.args[0] for c in engine.runner.call_args_list] == ['mobilenet', 'prithiv']
    assert all(c.args[1] is snapshot for c in engine.runner.call_args_list)
    assert state['summary']['match'] is True
    assert state['last_analysis']
    for result in state['results'].values():
        assert result['normalized_state'] == 'partly_cloudy'
        assert {'native_label', 'mapped_state', 'confidence', 'probabilities',
                'inference_seconds', 'rss_before_bytes', 'rss_after_bytes'} <= result.keys()
    assert all(v['phase'] == 'unloaded' for v in state['models'].values())


@pytest.mark.parametrize('daylight', [False, None])
def test_daylight_fail_closed_and_manual_bypass(snapshot, daylight):
    engine = make_engine(snapshot)
    engine.ha.daylight.return_value = daylight
    assert execute(engine)['skipped']
    engine.ha.snapshot.assert_not_called()
    assert execute(engine, bypass=True)['last_analysis']


def test_sun_failure_fails_closed(snapshot):
    engine = make_engine(snapshot)
    engine.ha.daylight.side_effect = RuntimeError('secret token')
    state = execute(engine)
    assert 'secret' not in str(state)
    engine.ha.snapshot.assert_not_called()


def test_interval_and_manual_independent(snapshot):
    engine = make_engine(snapshot)
    with patch.object(engine, 'start', return_value=True) as start:
        engine.tick(100)
        engine.tick(3699)
        assert start.call_count == 1
        engine.tick(3700)
        assert start.call_count == 2
    assert execute(engine)['last_analysis']


@pytest.mark.parametrize('label,state', [
    ('partly cloudy', 'partly_cloudy'), ('clear', 'sunny'), ('overcast', 'cloudy'),
    ('rainy', 'rain'), ('snowy', 'snow'), ('foggy', 'fog'), ('unknown', 'unknown'),
    ('cloudy/overcast', 'cloudy'), ('foggy/hazy', 'fog'), ('rain/strom', 'rain'),
    ('rain/storm', 'rain'), ('snow/frosty', 'snow'), ('sun/clear', 'sunny'),
    ('unexpected', 'unknown')])
def test_normalization(label, state):
    assert normalize({label: 1}, .65)['normalized_state'] == state


def test_threshold():
    result = normalize({'sun/clear': .64, 'cloudy/overcast': .36}, .65)
    assert result['normalized_state'] == 'unknown'
    assert result['native_label'] == 'sun/clear'
    assert result['mapped_state'] == 'sunny'
    assert result['probabilities']['sun/clear'] == .64
    assert normalize({'clear': .65, 'rainy': .35}, .65)['normalized_state'] == 'sunny'


@pytest.mark.parametrize('entity', ['camera.x/../../evil', 'https://evil', 'camera.foo?token=a'])
def test_entity_validation(entity):
    with pytest.raises(ValueError):
        Analysis({'camera_entity': entity}, Mock())


def test_api(snapshot):
    engine = make_engine(snapshot)
    client = create_app(engine).test_client()
    assert client.get('/').status_code == 200
    assert client.get('/api/status').json['camera_entity'] == 'camera.woonkamer_vloeiend'
    assert client.post('/api/analyze', data='x').status_code == 400
    assert client.post('/api/analyze', json={'bypass_daylight': 'yes'}).status_code == 400
    with patch.object(engine, 'start', return_value=True) as start:
        assert client.post('/api/analyze', json={'bypass_daylight': True}).status_code == 202
        start.assert_called_once_with(True)
    engine.lock.acquire()
    try:
        assert client.post('/api/analyze', json={}).status_code == 409
    finally:
        engine.lock.release()
    execute(engine)
    result = client.get('/api/status')
    assert result.headers['Cache-Control'] == 'no-store'
    assert set(result.json['results']) == {'mobilenet', 'prithiv'}
    assert 'PNG' not in result.get_data(as_text=True)


def test_invalid_image_and_size(snapshot):
    for data in [b'not an image', b'x' * (MAX_BYTES + 1)]:
        engine = make_engine(data)
        assert execute(engine)['error']
        engine.runner.assert_not_called()


def test_candidate_failure_does_not_skip_other(snapshot):
    engine = make_engine(snapshot)
    engine.runner.side_effect = [RuntimeError('secret'),
                                 dict(probabilities={'sun/clear': 1}, inference_seconds=.1)]
    state = execute(engine)
    assert engine.runner.call_count == 2
    assert 'error' in state['results']['mobilenet']
    assert state['results']['prithiv']['normalized_state'] == 'sunny'
    assert state['summary']['higher_confidence'] is None


def test_supervisor_calls(monkeypatch, snapshot):
    monkeypatch.setenv('SUPERVISOR_TOKEN', 'unit-test-secret')
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    response = Mock(status_code=200)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    session.get.return_value = response
    with patch('app.requests.Session', return_value=session):
        ha = HA()
        response.iter_content.return_value = [b'{"state":"above_horizon"}']
        assert ha.daylight()
        response.iter_content.return_value = [snapshot]
        assert ha.snapshot('camera.test') == snapshot
        assert session.trust_env is False
        args, kwargs = session.get.call_args
        assert args[0] == 'http://supervisor/core/api/camera_proxy/camera.test'
        assert kwargs['allow_redirects'] is False
        assert kwargs['headers']['Authorization'] == 'Bearer unit-test-secret'
        response.status_code = 302
        with pytest.raises(RuntimeError):
            ha.snapshot('camera.test')
        response.status_code = 200
        response.iter_content.return_value = [b'x' * (MAX_BYTES + 1)]
        with pytest.raises(RuntimeError):
            ha.snapshot('camera.test')


def test_ingress_peer(snapshot):
    client = create_app(make_engine(snapshot), ingress_only=True).test_client()
    assert client.get('/api/status').status_code == 403
    assert client.get('/api/status', headers={'X-Forwarded-For': '172.30.32.2'}).status_code == 403
    assert client.get('/api/status', environ_base={'REMOTE_ADDR': '172.30.32.2'}).status_code == 200


def test_worker_transport_and_token_removal(monkeypatch, snapshot):
    from app import run_candidate
    monkeypatch.setenv('SUPERVISOR_TOKEN', 'secret')
    with patch('app.subprocess.run') as run:
        run.return_value.stdout = b'{"probabilities":{"clear":1}}'
        assert run_candidate('mobilenet', snapshot)['probabilities'] == {'clear': 1}
        args, kwargs = run.call_args
        assert args[0][-1] == 'mobilenet'
        assert kwargs['input'] is snapshot
        assert kwargs['timeout'] == 900
        assert kwargs['check'] is True
        assert 'SUPERVISOR_TOKEN' not in kwargs['env']


def test_config_and_build_contract():
    import re
    import yaml
    from app import DEFAULTS
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / 'config.yaml').read_text())
    assert config['options'] == DEFAULTS
    assert set(config['schema']) == set(DEFAULTS)
    assert config['homeassistant_api'] is True
    assert config['ingress'] is True and config['ingress_port'] == 8099
    assert config['arch'] == ['aarch64', 'amd64']
    assert 'ports' not in config and 'image' not in config
    assert config['init'] is True
    assert re.fullmatch(config['schema']['camera_entity'][6:-1], DEFAULTS['camera_entity'])
    dockerfile = (root / 'Dockerfile').read_text()
    assert 'python:3.11-slim-bookworm' in dockerfile
    assert 'torch==2.6.0 torchvision==0.21.0' in dockerfile
    assert 'huggingface.co' not in dockerfile
    for filename in ['requirements.txt', 'app.py', 'models.py', 'worker.py', 'run.sh']:
        assert (root / filename).is_file()
