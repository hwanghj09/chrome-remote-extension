from fastapi.testclient import TestClient
from server.main import create_app

ADMIN_KEY = 'my-local-admin-key-please-replace-me'
HEADERS = {'X-Admin-Key': ADMIN_KEY}


def test_pair_and_targeted_commands(tmp_path):
    app = create_app(admin_key=ADMIN_KEY, database_path=str(tmp_path / 'test.sqlite'))
    with TestClient(app) as client:
        assert client.get('/health').json() == {'ok': True}
        assert client.get('/api/admin/devices').status_code == 401

        enrollment = client.post('/api/admin/enrollments', headers=HEADERS, json={'name': 'test-device'}).json()
        pair = client.post('/api/pair', json={'code': enrollment['pairing_code'], 'device_name': 'Chromebook A'})
        assert pair.status_code == 200
        credentials = pair.json()
        device = credentials['device_id']
        assert client.post('/api/pair', json={'code': enrollment['pairing_code'], 'device_name': 'again'}).status_code == 403
        assert client.post(f'/api/admin/devices/{device}/commands', headers=HEADERS,
                           json={'action': 'get_status'}).status_code == 409

        with client.websocket_connect('/ws/device') as ws:
            ws.send_json({'type': 'auth', 'device_id': device, 'device_token': credentials['device_token']})
            assert ws.receive_json()['type'] == 'ready'
            online = client.get('/api/admin/devices', headers=HEADERS).json()
            assert online[0]['online'] is True
            bad_url = client.post(f'/api/admin/devices/{device}/commands', headers=HEADERS,
                                  json={'action': 'open_tab', 'params': {'url': 'https://not-allowed.example'}})
            assert bad_url.status_code == 422
            bad_action = client.post(f'/api/admin/devices/{device}/commands', headers=HEADERS,
                                     json={'action': 'run_shell', 'params': {}})
            assert bad_action.status_code == 422
            sent = client.post(f'/api/admin/devices/{device}/commands', headers=HEADERS,
                               json={'action': 'open_tab', 'params': {'url': 'https://example.com/'}})
            assert sent.status_code == 200
            payload = ws.receive_json()
            assert payload['action'] == 'open_tab'
            assert payload['command_id'] == sent.json()['command_id']
            ws.send_json({'type': 'result', 'command_id': payload['command_id'],
                          'ok': True, 'data': {'tab': {'id': 123}}})
            ws.send_json({'type': 'ping'})
            assert ws.receive_json()['type'] == 'pong'
            result = client.get(f'/api/admin/commands/{payload["command_id"]}', headers=HEADERS).json()
            assert result['status'] == 'done'
            assert result['result']['data']['tab']['id'] == 123

        assert client.delete(f'/api/admin/devices/{device}', headers=HEADERS).json() == {'revoked': True}
        assert client.get('/api/admin/devices', headers=HEADERS).json() == []


def test_ws_invalid_token(tmp_path):
    app = create_app(admin_key=ADMIN_KEY, database_path=str(tmp_path / 'test.sqlite'))
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
