from fastapi.testclient import TestClient
from main import create_app


def test_register_and_targeted_commands(tmp_path):
    app = create_app(database_path=str(tmp_path / 'test.sqlite'))
    with TestClient(app) as client:
        assert client.get('/health').json() == {'ok': True}
        assert client.get('/api/admin/devices').json() == []

        pair = client.post('/api/register')
        assert pair.status_code == 200
        credentials = pair.json()
        device = credentials['device_id']
        assert client.post(f'/api/admin/devices/{device}/commands',
                           json={'action': 'get_status'}).status_code == 409

        with client.websocket_connect('/ws/device') as ws:
            ws.send_json({'type': 'auth', 'device_id': device, 'device_token': credentials['device_token']})
            assert ws.receive_json()['type'] == 'ready'
            online = client.get('/api/admin/devices').json()
            assert online[0]['online'] is True
            bad_url = client.post(f'/api/admin/devices/{device}/commands',
                                  json={'action': 'open_tab', 'params': {'url': 'ftp://example.com/'}})
            assert bad_url.status_code == 422
            bad_action = client.post(f'/api/admin/devices/{device}/commands',
                                     json={'action': 'run_shell', 'params': {}})
            assert bad_action.status_code == 422
            sent = client.post(f'/api/admin/devices/{device}/commands',
                               json={'action': 'open_tab', 'params': {'url': 'https://example.com/'}})
            assert sent.status_code == 200
            payload = ws.receive_json()
            assert payload['action'] == 'open_tab'
            assert payload['command_id'] == sent.json()['command_id']
            ws.send_json({'type': 'result', 'command_id': payload['command_id'],
                          'ok': True, 'data': {'tab': {'id': 123}}})
            ws.send_json({'type': 'ping'})
            assert ws.receive_json()['type'] == 'pong'
            result = client.get(f'/api/admin/commands/{payload["command_id"]}').json()
            assert result['status'] == 'done'
            assert result['result']['data']['tab']['id'] == 123

        assert client.delete(f'/api/admin/devices/{device}').json() == {'revoked': True}
        assert client.get('/api/admin/devices').json() == []


def test_ws_invalid_token(tmp_path):
    app = create_app(database_path=str(tmp_path / 'test.sqlite'))
    with TestClient(app) as client:
        with client.websocket_connect('/ws/device') as ws:
            ws.send_json({'type': 'auth', 'device_id': 'fake', 'device_token': 'fake'})
            from starlette.websockets import WebSocketDisconnect
            try:
                ws.receive_json()
            except WebSocketDisconnect as e:
                assert e.code == 1008
            else:
                raise AssertionError('Expected forbidden socket close')


def test_dashboard(tmp_path):
    app = create_app(database_path=str(tmp_path / 'dashboard.sqlite'))
    with TestClient(app) as client:
        page = client.get('/')
        assert page.status_code == 200
        assert 'Chrome Remote' in page.text
        assert "frame-ancestors 'none'" in page.headers['Content-Security-Policy']
        assert 'no-store' in page.headers['Cache-Control']
        assert client.get('/static/app.js').status_code == 200
        assert client.get('/api/admin/devices').json() == []


def test_numeric_ids_no_duplicates_or_reuse(tmp_path):
    app = create_app(database_path=str(tmp_path / 'pool.sqlite'))
    with TestClient(app) as client:
        assigned = set()
        for _ in range(100):
            paired = client.post('/api/register')
            assert paired.status_code == 200, paired.text
            device_id = paired.json()['device_id']
            assert device_id.isdecimal()
            assert 1 <= int(device_id) <= 100
            assert device_id not in assigned
            assigned.add(device_id)

        device = next(iter(assigned))
        assert client.delete(f'/api/admin/devices/{device}').status_code == 200
        last = client.post('/api/register')
        assert last.status_code == 409
        assert len(assigned) == 100
