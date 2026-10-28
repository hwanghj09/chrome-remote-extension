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
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).with_name('.env'))

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


class EnrollmentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    ttl_seconds: int = Field(default=300, ge=30, le=3600)


class PairRequest(BaseModel):
    code: str = Field(min_length=8, max_length=128)
    device_name: str = Field(min_length=1, max_length=60)


class CommandRequest(BaseModel):
    action: str
    params: dict = Field(default_factory=dict)


def create_app(*, admin_key: str | None = None, database_path: str | None = None) -> FastAPI:
    admin_secret = admin_key if admin_key is not None else os.getenv('ADMIN_API_KEY', '')
    db_path = Path(database_path or os.getenv('DATABASE_PATH', 'server/devices.db'))
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
        if not admin_secret or len(admin_secret) < 24:
            raise RuntimeError('Set a strong ADMIN_API_KEY (24+ characters).')
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
                CREATE TABLE IF NOT EXISTS enrollments (
                    code_hash TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
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

    def require_admin(x_admin_key: str | None = Header(default=None)):
        if not x_admin_key or not secrets.compare_digest(x_admin_key, admin_secret):
            raise HTTPException(status_code=401, detail='Invalid administrator key')

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

    @app.post('/api/admin/enrollments', dependencies=[Depends(require_admin)])
    def create_enrollment(payload: EnrollmentRequest):
        code = secrets.token_urlsafe(16)
        expires = int(time.time()) + payload.ttl_seconds
        with db() as conn:
            conn.execute('DELETE FROM enrollments WHERE expires_at < ?', (int(time.time()),))
            conn.execute('INSERT INTO enrollments VALUES (?, ?, ?)',
                         (digest(code), payload.name, expires))
        return {'pairing_code': code, 'expires_at': expires}

    @app.post('/api/pair')
    def pair_device(payload: PairRequest):
        with db() as conn:
            # Single-use pairing code; the DELETE and INSERT share one transaction.
            # Atomic one-time consumption prevents code reuse under concurrent requests.
            consumed = conn.execute('DELETE FROM enrollments WHERE code_hash=? AND expires_at>=?',
                                    (digest(payload.code), int(time.time()))).rowcount
            if not consumed:
                raise HTTPException(403, 'Invalid or expired pairing code')
            device_id = str(uuid.uuid4())
            token = secrets.token_urlsafe(32)
            now = int(time.time())
            conn.execute('INSERT INTO devices VALUES (?, ?, ?, ?, ?)',
                         (device_id, payload.device_name, digest(token), now, now))
        return {'device_id': device_id, 'device_token': token}

    @app.get('/api/admin/devices', dependencies=[Depends(require_admin)])
    def list_devices():
        with db() as conn:
            records = conn.execute('SELECT device_id, name, created_at, last_seen FROM devices ORDER BY created_at DESC').fetchall()
        return [{'device_id': x['device_id'], 'name': x['name'],
                 'online': x['device_id'] in connections,
                 'created_at': x['created_at'], 'last_seen': x['last_seen']} for x in records]

    @app.delete('/api/admin/devices/{device_id}', dependencies=[Depends(require_admin)])
    async def revoke_device(device_id: str):
        with db() as conn:
            count = conn.execute('DELETE FROM devices WHERE device_id=?', (device_id,)).rowcount
        if not count:
            raise HTTPException(404, 'Device not found')
        socket = connections.pop(device_id, None)
        if socket:
            await socket.close(code=1008, reason='Device revoked')
        return {'revoked': True}

    @app.post('/api/admin/devices/{device_id}/commands', dependencies=[Depends(require_admin)])
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

    @app.get('/api/admin/commands/{command_id}', dependencies=[Depends(require_admin)])
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
