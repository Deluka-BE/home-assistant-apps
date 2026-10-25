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
    return Analysis({'save_history': False} | options, ha, runner)


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


def test_history_save_jsonl_and_shared_bytes(snapshot, tmp_path):
    import json
    engine = make_engine(snapshot, save_history=True)
    engine.history_dir = tmp_path
    state = execute(engine)
    record = json.loads((tmp_path / 'analysis.jsonl').read_text())
    stored = tmp_path / 'images' / record['image_filename']
    assert stored.suffix == '.png'
    assert stored.read_bytes() == snapshot
    assert all(c.args[1] is snapshot for c in engine.runner.call_args_list)
    assert record['timestamp'] == state['last_analysis']
    assert record['camera_entity'] == state['camera_entity']
    assert record['results'] == state['results']
    assert record['summary'] == state['summary']
    assert state['history_status'] == 'saved' and state['history_error'] is None
    execute(engine, bypass=True)
    assert len((tmp_path / 'analysis.jsonl').read_text().splitlines()) == 2
    assert len(list((tmp_path / 'images').iterdir())) == 2


def test_history_retention_preserves_audit(snapshot, tmp_path):
    import json
    engine = make_engine(snapshot, save_history=True, history_max_images=24)
    engine.history_dir = tmp_path
    for _ in range(26):
        assert execute(engine)['history_status'] == 'saved'
    records = [json.loads(line) for line in (tmp_path / 'analysis.jsonl').read_text().splitlines()]
    images = {p.name for p in (tmp_path / 'images').iterdir()}
    assert len(records) == 26 and len(images) == 24
    assert records[0]['image_filename'] not in images
    assert records[-1]['image_filename'] in images


def test_disabled_history(snapshot, tmp_path):
    engine = make_engine(snapshot)
    engine.history_dir = tmp_path / 'unused'
    assert execute(engine)['history_status'] == 'disabled'
    assert not engine.history_dir.exists()


@pytest.mark.parametrize('failure', ['mkdir', 'write', 'audit', 'prune'])
def test_history_failure_isolation(snapshot, tmp_path, failure):
    engine = make_engine(snapshot, save_history=True)
    engine.history_dir = tmp_path
    if failure == 'audit':
        (tmp_path / 'analysis.jsonl').mkdir()
    method = {'mkdir': 'mkdir', 'write': 'open', 'prune': 'iterdir'}.get(failure)
    if method:
        with patch.object(Path, method, side_effect=OSError('secret details')):
            state = execute(engine)
    else:
        state = execute(engine)
    assert state['summary']['match'] is True and state['error'] is None
    assert state['history_status'] == 'error'
    assert state['history_error'] and 'secret' not in str(state)
    assert engine.runner.call_count == 2


def test_history_skipped_invalid_and_candidate_error(snapshot, tmp_path):
    import json
    engine = make_engine(snapshot, save_history=True)
    engine.history_dir = tmp_path
    engine.ha.daylight.return_value = False
    execute(engine)
    assert not list(tmp_path.iterdir())
    engine.ha.snapshot.return_value = b'invalid'
    execute(engine, bypass=True)
    assert not list(tmp_path.iterdir())
    engine.ha.snapshot.return_value = snapshot
    engine.runner.side_effect = [RuntimeError('secret'), dict(probabilities={'clear': 1})]
    state = execute(engine, bypass=True)
    record = json.loads((tmp_path / 'analysis.jsonl').read_text())
    assert record['results']['mobilenet']['error'] == state['results']['mobilenet']['error']
    assert 'secret' not in str(record)


@pytest.mark.parametrize('options', [dict(save_history=1), dict(history_max_images=23),
                                    dict(history_max_images=5001), dict(history_max_images=True)])
def test_history_options_validation(options):
    with pytest.raises(ValueError):
        Analysis(options, Mock())


def test_sensor_results_publish_before_next_candidate(snapshot):
    from models import CANDIDATES
    engine = make_engine(snapshot)
    original = engine.runner.side_effect

    def run(name, data):
        if name == 'prithiv':
            assert engine.ha.publish_sensor.call_count == 1
        return original(name, data) | {'peak_rss_bytes': 1234}

    engine.runner.side_effect = run
    state = execute(engine)
    assert engine.ha.publish_sensor.call_count == 2
    for call, name in zip(engine.ha.publish_sensor.call_args_list, CANDIDATES):
        candidate, normalized, attrs = call.args
        assert candidate == name and normalized == 'partly_cloudy'
        assert attrs['native_label'] == 'partly cloudy'
        assert attrs['mapped_state'] == 'partly_cloudy'
        assert attrs['confidence'] == .8
        assert attrs['inference_seconds'] == .2
        assert attrs['load_and_inference_seconds'] == .4
        assert attrs['peak_rss_bytes'] == 1234
        assert attrs['last_analysis'] and attrs['camera_entity'] == state['camera_entity']
        assert (attrs['repository'], attrs['revision']) == CANDIDATES[name]
        assert state['sensor_publications'][name]['error'] is None


def test_sensor_unknown_threshold_and_candidate_failure(snapshot):
    engine = make_engine(snapshot, minimum_confidence=.9)
    execute(engine)
    assert all(c.args[1] == 'unknown' for c in engine.ha.publish_sensor.call_args_list)
    engine.ha.publish_sensor.reset_mock()
    engine.runner.side_effect = [RuntimeError('secret payload'), dict(probabilities={'clear': 1})]
    execute(engine)
    failed, succeeded = engine.ha.publish_sensor.call_args_list
    assert failed.args[1] == 'unknown'
    assert failed.args[2]['error'] and 'secret' not in str(failed)
    assert failed.args[2]['confidence'] is None
    assert failed.args[2]['peak_rss_bytes'] is None
    assert succeeded.args[1] == 'sunny'
    assert 'error' not in succeeded.args[2]


def test_sensor_failure_preserves_results_and_history(snapshot, tmp_path):
    engine = make_engine(snapshot, save_history=True)
    engine.history_dir = tmp_path
    engine.ha.publish_sensor.side_effect = [RuntimeError('secret'), None]
    state = execute(engine)
    assert state['summary']['match'] and state['history_status'] == 'saved'
    assert engine.runner.call_count == engine.ha.publish_sensor.call_count == 2
    assert state['sensor_publications']['mobilenet']['error']
    assert state['sensor_publications']['prithiv']['error'] is None
    assert 'secret' not in str(state)


@pytest.mark.parametrize('status', [200, 201, 302, 401, 500])
def test_sensor_rest_transport(monkeypatch, status):
    monkeypatch.setenv('SUPERVISOR_TOKEN', 'test-token')
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    response = Mock(status_code=status)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    session.post.return_value = response
    with patch('app.requests.Session', return_value=session):
        ha = HA()
        if status in (200, 201):
            ha.publish_sensor('prithiv', 'rain', {'confidence': .9})
        else:
            with pytest.raises(RuntimeError, match='sensor update failed'):
                ha.publish_sensor('prithiv', 'rain', {'confidence': .9})
    args, kwargs = session.post.call_args
    assert args == ('http://supervisor/core/api/states/sensor.weather_camera_prithiv',)
    assert kwargs['json'] == {'state': 'rain', 'attributes': {'confidence': .9}}
    assert kwargs['headers'] == {'Authorization': 'Bearer test-token'}
    assert kwargs['allow_redirects'] is False and kwargs['stream'] is True
    assert kwargs['timeout'] == (5, 20) and session.trust_env is False


def test_no_sensor_updates_without_candidate_attempt(snapshot):
    engine = make_engine(snapshot)
    engine.ha.daylight.return_value = False
    execute(engine)
    engine.ha.snapshot.return_value = b'invalid'
    execute(engine, bypass=True)
    engine.ha.publish_sensor.assert_not_called()
