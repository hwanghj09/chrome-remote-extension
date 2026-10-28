"""Opt-in Chrome extension fleet controller (FastAPI + SQLite + WebSocket)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')

# The API never accepts arbitrary JavaScript, shell commands, or device data collection.
COMMAND_PARAMS = {
    'get_status': set(),
    'list_tabs': set(),
    'open_tab': {'url'},
    'close_tab': {'tab_id'},
    'activate_tab': {'tab_id'},
    'reload_tab': {'tab_id'},
    'duplicate_tab': {'tab_id'},
    'navigate_tab': {'tab_id', 'url'},
    'pin_tab': {'tab_id', 'pinned'},
    'mute_tab': {'tab_id', 'muted'},
    'set_zoom': {'tab_id', 'zoom'},
    'list_windows': set(),
    'new_window': {'url'},
    'focus_window': {'window_id'},
    'close_window': {'window_id'},
    'notify': {'title', 'message'},
}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class CommandRequest(BaseModel):
    action: str
    params: dict = Field(default_factory=dict)


def create_app(*, admin_key: str | None = None, database_path: str | None = None) -> FastAPI:
    # admin_key is accepted for backward compatibility but no longer required.
    db_path = Path(database_path or os.getenv('DATABASE_PATH', 'data/devices.db'))
    allowed_hosts = {
        host.strip().lower()
        for host in os.getenv('ALLOWED_NAV_HOSTS', 'example.com').split(',')
        if host.strip()
    }
    connections: dict[str, WebSocket] = {}

    def db() -> sqlite3.Connection:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with db() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assigned_ids (
                    device_id TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    created_at INTEGER NOT NULL
                );
            ''')
        yield
        for socket in list(connections.values()):
            try:
                await socket.close()
            except Exception:
                pass

    app = FastAPI(title='Chrome Remote Controller', lifespan=lifespan)
    app.mount('/static', StaticFiles(directory=ROOT / 'web'), name='static')

    @app.get('/', include_in_schema=False)
    def dashboard():
        response = FileResponse(ROOT / 'web' / 'index.html')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = ("default-src 'none'; script-src 'self'; style-src 'self'; "
                                                       "connect-src 'self'; img-src 'self'; base-uri 'none'; "
                                                       "frame-ancestors 'none'; form-action 'none'")
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    def validate_command(command: CommandRequest):
        expected = COMMAND_PARAMS.get(command.action)
        if expected is None:
            raise HTTPException(422, 'Unsupported action')
        if set(command.params) != expected:
            raise HTTPException(422, f'Expected params: {sorted(expected)}')
        p = command.params
        for key in ('tab_id', 'window_id'):
            if key in p and (type(p[key]) is not int or p[key] < 0):
                raise HTTPException(422, f'{key} must be a nonnegative integer')
        for key in ('pinned', 'muted'):
            if key in p and type(p[key]) is not bool:
                raise HTTPException(422, f'{key} must be boolean')
        if 'zoom' in p and (type(p['zoom']) not in (float, int) or not 0.25 <= p['zoom'] <= 5.0):
            raise HTTPException(422, 'zoom must be between 0.25 and 5.0')
        if 'url' in p:
            if not isinstance(p['url'], str) or len(p['url']) > 2000:
                raise HTTPException(422, 'Invalid URL')
            parsed = urlsplit(p['url'])
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                    parsed.username or parsed.password or parsed.hostname.lower() not in allowed_hosts):
                raise HTTPException(422, 'URL hostname not in ALLOWED_NAV_HOSTS')
        for key in ('title', 'message'):
            if key in p and (not isinstance(p[key], str) or not 1 <= len(p[key]) <= 200):
                raise HTTPException(422, f'{key} must contain 1–200 characters')

    @app.get('/health')
    def health():
        return {'ok': True}

    @app.post('/api/register')
    def register_device():
        # No pairing code and no device name: a device auto-registers on first connect.
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')  # serialize concurrent allocations across requests
            # Keep IDs small but unique, even across revocations. The DB is the authority.
            # The write transaction is exclusive during allocation to prevent collisions.
            already_assigned = {
                row['device_id'] for row in conn.execute('SELECT device_id FROM assigned_ids')
            }
            # Also respect numeric IDs created before this table existed.
            already_assigned.update(
                row['device_id'] for row in conn.execute('SELECT device_id FROM devices')
            )
            available = [str(n) for n in range(1, 101) if str(n) not in already_assigned]
            if not available:
                raise HTTPException(409, 'All 100 device IDs have been assigned')
            device_id = secrets.choice(available)
            token = secrets.token_urlsafe(32)
            now = int(time.time())
            conn.execute('INSERT INTO assigned_ids (device_id) VALUES (?)', (device_id,))
            conn.execute('INSERT INTO devices VALUES (?, ?, ?, ?, ?)',
                         (device_id, f'device-{device_id}', digest(token), now, now))
        return {'device_id': device_id, 'device_token': token}

    @app.get('/api/admin/devices')
    def list_devices():
        with db() as conn:
            records = conn.execute('SELECT device_id, name, created_at, last_seen FROM devices ORDER BY created_at DESC').fetchall()
        return [{'device_id': x['device_id'], 'name': x['name'],
                 'online': x['device_id'] in connections,
                 'created_at': x['created_at'], 'last_seen': x['last_seen']} for x in records]

    @app.delete('/api/admin/devices/{device_id}')
    async def revoke_device(device_id: str):
        with db() as conn:
            count = conn.execute('DELETE FROM devices WHERE device_id=?', (device_id,)).rowcount
        if not count:
            raise HTTPException(404, 'Device not found')
        socket = connections.pop(device_id, None)
        if socket:
            await socket.close(code=1008, reason='Device revoked')
        return {'revoked': True}

    @app.post('/api/admin/devices/{device_id}/commands')
    async def send_command(device_id: str, command: CommandRequest):
        validate_command(command)
        with db() as conn:
            if not conn.execute('SELECT 1 FROM devices WHERE device_id=?', (device_id,)).fetchone():
                raise HTTPException(404, 'Device not found')
        socket = connections.get(device_id)
        if not socket:
            raise HTTPException(409, 'Device is offline')
        command_id = str(uuid.uuid4())
        with db() as conn:
            conn.execute('INSERT INTO commands VALUES (?, ?, ?, ?, ?, ?)',
                         (command_id, device_id, command.action, 'queued', None, int(time.time())))
        try:
            await socket.send_json({'type': 'command', 'command_id': command_id,
                                    'action': command.action, 'params': command.params})
        except Exception:
            with db() as conn:
                conn.execute('UPDATE commands SET status=? WHERE command_id=?', ('delivery_failed', command_id))
            if connections.get(device_id) is socket:
                connections.pop(device_id, None)
            raise HTTPException(409, 'Device disconnected during send')
        with db() as conn:
            conn.execute("UPDATE commands SET status='sent' WHERE command_id=? AND status='queued'", (command_id,))
        return {'command_id': command_id, 'status': 'sent'}

    @app.get('/api/admin/commands/{command_id}')
    def get_command(command_id: str):
        with db() as conn:
            row = conn.execute('SELECT * FROM commands WHERE command_id=?', (command_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Command not found')
        return {'command_id': row['command_id'], 'device_id': row['device_id'],
                'action': row['action'], 'status': row['status'],
                'result': json.loads(row['result_json']) if row['result_json'] else None,
                'created_at': row['created_at']}

    @app.websocket('/ws/device')
    async def device_socket(socket: WebSocket):
        await socket.accept()
        device_id = None
        try:
            hello = await asyncio.wait_for(socket.receive_json(), timeout=10)
            if hello.get('type') != 'auth':
                await socket.close(code=1008)
                return
            device_id = hello.get('device_id')
            token = hello.get('device_token')
            if not isinstance(device_id, str) or not isinstance(token, str):
                await socket.close(code=1008)
                return
            with db() as conn:
                row = conn.execute('SELECT token_hash FROM devices WHERE device_id=?', (device_id,)).fetchone()
            if not row or not secrets.compare_digest(row['token_hash'], digest(token)):
                await socket.close(code=1008)
                return
            old_socket = connections.get(device_id)
            if old_socket:
                await old_socket.close(code=1000, reason='New connection')
            connections[device_id] = socket
            await socket.send_json({'type': 'ready', 'device_id': device_id})
            while True:
                message = await socket.receive_json()
                now = int(time.time())
                with db() as conn:
                    conn.execute('UPDATE devices SET last_seen=? WHERE device_id=?', (now, device_id))
                if message.get('type') == 'ping':
                    await socket.send_json({'type': 'pong'})
                elif message.get('type') == 'result':
                    cid = message.get('command_id')
                    if not isinstance(cid, str):
                        continue
                    result = {'ok': message.get('ok') is True,
                              'data': message.get('data') if isinstance(message.get('data'), dict) else {},
                              'error': str(message.get('error', ''))[:300]}
                    with db() as conn:
                        conn.execute("""UPDATE commands SET status=?, result_json=?
                                        WHERE command_id=? AND device_id=? AND status IN ('queued','sent')""",
                                     ('done' if result['ok'] else 'error', json.dumps(result), cid, device_id))
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, RuntimeError):
            pass
        finally:
            if device_id and connections.get(device_id) is socket:
                connections.pop(device_id, None)

    return app


app = create_app()
