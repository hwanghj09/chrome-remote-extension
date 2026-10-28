# Chrome Remote Extension

승인된 크롬/크롬북에서만 작동하는 **FastAPI + WebSocket + Manifest V3** 원격 브라우저 제어 예제입니다. 기기별 등록 코드, 개별 인증 토큰, 관리자 인증, 일시 정지, 개별 명령 실행 결과 조회를 제공합니다.

> **학교 관리형 크롬북:** 학교 IT 관리자의 승인을 받아 설치·운영하세요. 조직 정책이 확장 프로그램 설치 또는 특정 기능을 금지하면 사용할 수 없으며, 이 프로젝트에는 정책 우회 기능이 없습니다. ChromeOS 전체 원격 데스크톱 제어 도구가 아닙니다.

## 프로젝트 구조

```text
server/main.py           # FastAPI, SQLite, 기기 등록 및 WebSocket
server/requirements.txt
server/.env.example       # 비밀키 예시 (실제 .env는 Git에 올리지 않음)
extension/manifest.json
extension/background.js   # 허용된 명령만 실행
extension/popup.html
extension/popup.js        # 명시적 등록, 일시 정지
extension/icons/icon.png
scripts/admin_cli.py      # 등록/명령 전송 CLI
tests/test_server.py
```

## 1. 서버 실행

Python 3.11 이상에서 저장소 루트 기준:

```bash
pip install -r server/requirements.txt
cp server/.env.example server/.env
python -c "import secrets; print(secrets.token_urlsafe(32))"
# 출력된 랜덤 값을 server/.env 의 ADMIN_API_KEY 에 입력하세요.
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Windows PowerShell에서는 `cp server/.env.example server/.env`를 그대로 사용해도 됩니다. 서버 배포 시 `--host 0.0.0.0` 옵션, HTTPS/WSS를 제공하는 리버스 프록시와 접근 통제 설정이 필요합니다. **비밀키와 SQLite DB는 공개 저장소에 올리지 마세요.** SQLite 저장소는 단일 Uvicorn 프로세스 기반 데모 용도이며, 다중 워커를 사용할 경우 연결 레지스트리/명령 큐를 Redis 등 공유 인프라로 교체해야 합니다.

`ALLOWED_NAV_HOSTS`에 적힌 도메인만 원격으로 열 수 있습니다. 기본값은 `example.com,developer.chrome.com`입니다. 필요하다면 본인이 관리하는 사이트만 추가하세요.

## 2. 확장 프로그램 등록

1. Chrome `chrome://extensions` → 개발자 모드 → `extension/` 폴더 로드 (학교 관리형은 관리자 승인 필요)
2. 관리자 터미널에서 환경변수 `ADMIN_API_KEY`를 서버의 값과 동일하게 설정합니다.
3. `python scripts/admin_cli.py enroll "테스트 크롬북 A"` 실행 후 발급된 **1회용 코드**를 확인합니다 (5분 유효).
4. 확장 프로그램 아이콘 → 서버 주소(로컬 테스트 `http://127.0.0.1:8000`, 원격 `https://your-domain`)와 기기 이름·등록 코드 입력 → **연결 및 원격 제어 동의** 클릭.
5. 사이트 접근 권한 요청이 표시되면 검토 후 허용하세요. 이후 상태가 **ON**으로 보이면 서버 연결 상태입니다. 사용자는 언제든 **일시 정지**할 수 있습니다.

**주의:** 크롬북의 `127.0.0.1`은 크롬북 자신입니다. 다른 컴퓨터의 서버에는 HTTPS 주소로 접속해야 합니다. 외부 서버는 올바른 인증서를 사용하는 `https://`/`wss://`가 필요합니다. 일반 웹페이지의 개발자 모드가 아니라 `chrome://extensions`의 확장 프로그램 개발자 모드를 말합니다.

## 3. 등록된 기기 목록과 개별 제어

```bash
python scripts/admin_cli.py devices
```

출력되는 각 기기의 `device_id`(UUID)를 사용합니다. **명령 대상은 경로에서 지정한 한 기기뿐**입니다.

```bash
# 특정 크롬북에 새 탭 열기
python scripts/admin_cli.py send DEVICE_ID open_tab '{"url":"https://example.com/"}'

# 지정 기기의 현재 탭 개수 등 확인 (URL/제목은 수집하지 않음)
python scripts/admin_cli.py send DEVICE_ID get_status

# 기기에 브라우저 알림 표시
python scripts/admin_cli.py send DEVICE_ID notify '{"title":"안내","message":"테스트 알림입니다."}'

# 위 명령 출력에 나온 command_id 로 실제 결과 확인
python scripts/admin_cli.py result COMMAND_ID

# 등록 취소 및 인증 차단
python scripts/admin_cli.py revoke DEVICE_ID
```

Windows PowerShell에서 JSON 문자열 인용 규칙이 다를 수 있습니다. CLI 명령 대신 아래 HTTP API 및 Swagger UI(`/docs`)를 이용할 수도 있습니다.

| HTTP | 경로 | 용도 |
|---|---|---|
| POST | `/api/admin/enrollments` | 1회용 등록 코드 발급 |
| POST | `/api/pair` | 크롬북 본인의 등록 동의 및 토큰 발급 |
| GET | `/api/admin/devices` | 기기 목록과 온라인 상태 |
| DELETE | `/api/admin/devices/{device_id}` | 기기 등록 폐기 |
| POST | `/api/admin/devices/{device_id}/commands` | 지정 기기에 명령 전달 |
| GET | `/api/admin/commands/{command_id}` | 명령 실행 결과 조회 |
| WS | `/ws/device` | 확장 프로그램과 연결 |

`/api/admin/*` 요청에는 `X-Admin-Key: <ADMIN_API_KEY>` 헤더가 필요합니다. 기기 연결은 `device_id`와 별도의 `device_token`으로 인증합니다. 서버는 토큰의 해시만 저장합니다.

## 지원 명령

아래 `action`과 `params`만 허용됩니다. 임의 코드 실행은 지원하지 않습니다.

| action | params 예시 | 기능 |
|---|---|---|
| `get_status` | `{}` | OS 종류, 탭·창 개수 |
| `list_tabs` | `{}` | 탭 ID 및 상태 (URL, 제목 제외) |
| `open_tab` | `{"url":"https://example.com/"}` | 새 탭 열기 |
| `close_tab` | `{"tab_id":123}` | 탭 닫기 |
| `activate_tab` | `{"tab_id":123}` | 탭 활성화 |
| `reload_tab` | `{"tab_id":123}` | 탭 새로고침 |
| `duplicate_tab` | `{"tab_id":123}` | 탭 복제 |
| `navigate_tab` | `{"tab_id":123,"url":"https://example.com/"}` | 탭 이동 |
| `pin_tab` | `{"tab_id":123,"pinned":true}` | 탭 고정/해제 |
| `mute_tab` | `{"tab_id":123,"muted":true}` | 탭 음소거/해제 |
| `set_zoom` | `{"tab_id":123,"zoom":1.25}` | 탭 확대/축소 |
| `list_windows` | `{}` | 창 ID 및 상태 |
| `new_window` | `{"url":"https://example.com/"}` | 새 창 열기 |
| `focus_window` | `{"window_id":1}` | 창 활성화 |
| `close_window` | `{"window_id":1}` | 창 닫기 |
| `notify` | `{"title":"안내","message":"테스트"}` | 알림 표시 |

참고: 확장 프로그램 설치·권한·URL 접근은 학교 브라우저 정책의 영향을 받습니다. 기기를 절전하거나 네트워크가 끊기면 즉시 실행되지 않습니다. 서버는 오프라인 기기 명령을 큐에 저장하지 않고 `409`를 반환합니다. 서버의 `sent`는 전달 시도를 뜻하며, 실제 성공 여부는 `result`에서 확인하세요.

## 보안 및 개인정보 보호

- 팝업에서 **직접 등록하고 원격 제어 동의**를 해야 연결됩니다. 일시 정지하거나 등록 해제할 수 있습니다.
- 관리자·기기 인증은 서로 다른 비밀키를 사용하며, 등록 코드는 일회용·시간제한입니다.
- 서버 명령은 고정된 기능 목록에 한정되고, URL 열기는 도메인 허용목록을 사용합니다.
- 방문 기록, 페이지 주소·제목, 쿠키, 비밀번호, 키 입력, 화면 캡처 또는 임의 JavaScript/OS 명령을 전송하지 않습니다.
- 원격 운영은 HTTPS/WSS 및 학교 관리자 승인 하에서만 하세요. Chrome 확장 프로그램으로 ChromeOS 자체 정책이나 OS 수준 제한을 우회할 수 없습니다.

## 테스트

```bash
pip install pytest httpx
pytest -q
node --check extension/background.js
node --check extension/popup.js
```

Chrome 116 이상 WebSocket 서비스 워커 지원: https://developer.chrome.com/docs/extensions/how-to/web-platform/websockets
Chrome 확장 프로그램 권한: https://developer.chrome.com/docs/extensions/develop/concepts/declare-permissions
