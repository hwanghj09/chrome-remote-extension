# Chrome Remote · Web Console

학교 관리자의 승인을 받은 크롬/크롬북을 원격으로 제어하는 **FastAPI + WebSocket + Chrome Extension (MV3)** 예제입니다.

- Cloudtype 주소: https://port-0-chrome-remote-extension-iad5e2alq4tgcen.sel4.cloudtype.app/
- `/`: 웹 관리자 대시보드 (비밀번호 없이 접속, 기기 목록, 개별 기기 명령, 결과 조회 및 기기 등록 폐기)
- `/health`: 서버 상태 확인
- `/docs`: Swagger API 문서
- **기기 ID: 1~100 중 중복되지 않는 랜덤 숫자**. 등록 폐기한 번호도 재사용하지 않음. 100개 소진 시 새 등록은 HTTP 409로 거절. 번호를 초기화하려면 데이터베이스 관리자가 명시적으로 초기화해야 함.
- 기기는 **확장 프로그램에서 연결하면 등록 코드 없이 자동으로 서버에 등록**됩니다. 사용자는 언제든 일시 정지/등록 해제할 수 있습니다.
- 관리자 권한이 있는 기기에서만 사용하세요. 학교 관리 정책을 우회하거나 동의를 생략한 무단 제어 기능은 제공하지 않습니다.

## 파일 구조

```text
main.py                  # FastAPI 애플리케이션 (저장소 루트)
requirements.txt         # Cloudtype 자동 설치용 의존성
.env.example             # 환경변수 예시 (실제 키는 Cloudtype Secrets)
web/index.html           # 관리자 콘솔
web/styles.css
web/app.js
extension/manifest.json  # Chrome 확장 프로그램
extension/background.js  # 기기 연결 및 명령 처리
extension/popup.html
extension/popup.js       # 기기 등록, 일시 정지, 등록 해제
scripts/admin_cli.py     # 터미널 관리자 도구 (선택)
tests/test_server.py
```

## Cloudtype 배포 설정

GitHub의 `main` 브랜치를 Cloudtype **FastAPI** 템플릿으로 배포하세요. 기존에 하위 디렉터리로 되어 있던 배포 루트 경로 설정은 해제합니다.

| 설정 | 값 |
|---|---|
| Source directory | 저장소 최상단 (root) |
| Install command | `pip install -r requirements.txt` |
| Start command | `uvicorn main:app --host=0.0.0.0 --port=8000` |
| Port | `8000` |
| Health check | `/health` |
| Workers / replicas | **1** (SQLite와 메모리 연결 관리자 사용) |
| Environment variable | `DATABASE_PATH=/data/devices.db` (영구 볼륨 마운트한 경우에만) |

**Cloudtype 유의사항**: Cloudtype의 배포 저장소가 임시 파일시스템이면 재배포 시 SQLite 기기 등록 정보 및 1~100 번호 사용 기록이 모두 초기화됩니다. 연속 사용 시 **영구 볼륨**을 만들고 `DATABASE_PATH`를 실제 마운트 경로로 지정하세요. Cloudtype 설정 또는 기존 배포가 자동 갱신되었는지는 GitHub 업로드만으로 보장할 수 없으며 Cloudtype 재배포 상태를 확인해야 합니다.

Python 3.11 이상으로 로컬에서 실행한다면:

```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --host 127.0.0.1 --port 8000
```

## 웹 관리자 콘솔 사용법

1. Cloudtype 서버의 `/` 주소에 접속합니다. 비밀번호 입력 없이 바로 콘솔이 열립니다.
2. 학교 관리자가 설치를 허용한 크롬북에서 확장 프로그램을 열고, 미리 입력된 Cloudtype 서버 주소를 확인해 **연결 및 원격 제어 동의**를 누릅니다.
3. 연결하면 등록 코드 없이 **1~100 중 사용되지 않은 랜덤 번호**를 자동으로 받고 관리자 콘솔 목록에 나타납니다. 번호를 눌러 해당 기기만 선택합니다.
4. 탭/창 제어, 브라우저 알림, 상태 조회 등 명령을 내리고 실행 결과를 확인합니다.
5. 사용자는 확장 프로그램 팝업에서 일시 정지하거나 등록 해제할 수 있고, 관리자도 기기 등록을 폐기할 수 있습니다.

**관리형 크롬북**: 학교 IT 관리자의 설치/권한 승인과 네트워크 허용이 필요합니다. 기기별 숫자는 식별자일 뿐이며, 숫자를 안다고 제어할 수는 없습니다. 별도의 기기 토큰과 관리자 키로 인증합니다.

## API

`/api/admin/*` 호출은 비밀번호 없이 사용합니다. WebSocket 연결에는 기기별 토큰을 별도로 사용합니다.

| HTTP | URL | 설명 |
|---|---|---|
| POST | `/api/register` | 확장 프로그램이 연결 시 자동 등록 (코드/이름 불필요) |
| GET | `/api/admin/devices` | 기기 목록 / 접속 상태 |
| DELETE | `/api/admin/devices/{device_id}` | 특정 기기 등록 폐기 |
| POST | `/api/admin/devices/{device_id}/commands` | **특정 기기에만** 명령 전달 |
| GET | `/api/admin/commands/{command_id}` | 명령 수행 결과 확인 |
| WS | `/ws/device` | 등록된 크롬 확장 프로그램 연결 |

### 원격 제어 기능

- 기기 상태: `get_status` (OS와 탭/창 개수)
- 탭: `list_tabs`, `open_tab`, `close_tab`, `activate_tab`, `reload_tab`, `duplicate_tab`, `navigate_tab`, `pin_tab`, `mute_tab`, `set_zoom`
- 창: `list_windows`, `new_window`, `focus_window`, `close_window`
- 알림: `notify`

`open_tab`, `navigate_tab`, `new_window`에 지정하는 URL은 일반 http/https 주소면 모두 허용됩니다. 브라우저 내부 URL(chrome:// 등)과 임의 코드 명령은 거절됩니다. 탭 목록은 ID, 활성화, 고정, 음소거 정보만 제공하고 URL/제목을 서버로 전송하지 않습니다.

### 터미널에서 기기 제어 (선택)

```bash
export SERVER_URL="https://port-0-chrome-remote-extension-iad5e2alq4tgcen.sel4.cloudtype.app"
python scripts/admin_cli.py devices
python scripts/admin_cli.py send 37 open_tab '{"url":"https://example.com/"}'
```

## 제약 및 안전

- 이 버전은 웹 관리자 콘솔에서 명령을 내리는 방식이며, **실시간 화면 스트리밍이나 실제 마우스 커서의 원격 이동은 구현하지 않았습니다.** ChromeOS 전체를 제어하는 도구가 아닙니다.
- Chrome의 MV3 background service worker는 중단될 수 있어 WebSocket 재연결과 heartbeat를 구현했지만 연결 지속은 보장되지 않습니다.
- 관리자는 기기/브라우저 권한에 따른 한계를 따라야 합니다. 학교의 기기 관리 정책 차단을 우회하지 않습니다.
- 비밀번호, 쿠키, 방문 기록, 화면 녹화, 임의 JavaScript 실행 기능은 포함하지 않습니다.
- 현재는 **단일 FastAPI 프로세스 + SQLite** 예제입니다. 다중 인스턴스로 확장하려면 PostgreSQL과 공유 WebSocket 명령 브로커 등이 필요합니다.

## 개발 테스트

```bash
pip install pytest httpx
python -m pytest -q
node --check web/app.js
node --check extension/background.js
node --check extension/popup.js
```

Cloudtype FastAPI 배포 문서: https://docs.cloudtype.dev/ko/developers/fastapi
