"""Simple admin CLI for a locally hosted instance. Requires ADMIN_API_KEY."""
import argparse
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get('SERVER_URL', 'http://127.0.0.1:8000').rstrip('/')
KEY = os.environ.get('ADMIN_API_KEY')


def call(method, path, payload=None):
    if not KEY:
        raise SystemExit('Set ADMIN_API_KEY before running admin_cli.py')
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = urllib.request.Request(
        BASE + path, data=body, method=method,
        headers={'X-Admin-Key': KEY, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print(json.dumps(json.load(response), indent=2, ensure_ascii=False))
    except urllib.error.HTTPError as error:
        print(f'HTTP {error.code}: {error.read().decode()}')
        raise SystemExit(1) from error


parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest='cmd', required=True)
enroll = sub.add_parser('enroll')
enroll.add_argument('name')
sub.add_parser('devices')
send = sub.add_parser('send')
send.add_argument('device_id')
send.add_argument('action')
send.add_argument('params_json', nargs='?', default='{}')
result = sub.add_parser('result')
result.add_argument('command_id')
revoke = sub.add_parser('revoke')
revoke.add_argument('device_id')
args = parser.parse_args()

if args.cmd == 'enroll':
    call('POST', '/api/admin/enrollments', {'name': args.name})
elif args.cmd == 'devices':
    call('GET', '/api/admin/devices')
elif args.cmd == 'send':
    call('POST', f'/api/admin/devices/{args.device_id}/commands',
         {'action': args.action, 'params': json.loads(args.params_json)})
elif args.cmd == 'result':
    call('GET', f'/api/admin/commands/{args.command_id}')
elif args.cmd == 'revoke':
    call('DELETE', f'/api/admin/devices/{args.device_id}')
