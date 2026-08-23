# TimeTree MCP

TimeTree MCP 是一個非官方的 [Model Context Protocol](https://modelcontextprotocol.io/) server，可透過 HTTPS 存取 TimeTree 行事曆。預設使用 Python direct client，不會啟動瀏覽器；browser transport 僅保留作為相容備援。session cookie 與 CSRF token 只存在 MCP process 記憶體中。

> **重要：** TimeTree 官方 Connect App／API 已終止。本專案使用 TimeTree Web 的非公開 endpoint，服務改版時可能失效。請自行評估風險並遵守 TimeTree 的條款與政策。

## 功能

- 讀取 calendars、events、memos、comments、labels 與 members。
- 建立、更新、刪除 events 與 memos。
- 新增、更新、刪除 event comments。
- 以 merge 方式更新 calendar labels。
- 背景監控 calendar 變更並發送 MCP notifications。
- 所有寫入操作都要求明確傳入 `confirm: true`。
- 不把 credentials 放進 MCP tool arguments，authenticated session 只保存在記憶體。

## 需求

- Python 3.10 或更新版本。
- TimeTree 帳號。
- macOS 才能使用 Keychain helper；其他平台可使用環境變數或 password file。

## 安裝

```bash
git clone <repository-url>
cd timetree-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
```

Runtime 只使用 Python standard library。安裝後會提供
`timetree-mcp` 與 `timetree-credentials` 指令。

## 設定 credentials

macOS 建議使用 Keychain。設定時密碼只會在終端隱藏輸入，不會進入 shell
history、MCP arguments 或 source code：

```bash
./scripts/setup_timetree_keychain.sh
timetree-credentials status
```

MCP 會自動讀取 Keychain 中的 email/password。status 只回報是否存在，不會
回傳值；清除兩個 Keychain item：

```bash
timetree-credentials clear
```

若不使用 Keychain，也可使用環境變數：

```bash
export TIMETREE_EMAIL='your-email@example.com'
export TIMETREE_PASSWORD='your-password'
```

更建議使用權限為 600 的 password file：

```bash
chmod 600 /absolute/path/to/timetree-password
export TIMETREE_EMAIL='your-email@example.com'
export TIMETREE_PASSWORD_FILE='/absolute/path/to/timetree-password'
```

## 註冊到 Codex

把 `/absolute/path/to/timetree-mcp` 替換成 checkout 位置。若已存在同名
`timetree` server，請先移除再重新加入：

```bash
codex mcp remove timetree
codex mcp add timetree \
  --env TIMETREE_EMAIL=your-email@example.com \
  --env TIMETREE_PASSWORD_FILE=/absolute/path/to/timetree-password \
  -- python3 /absolute/path/to/timetree-mcp/timetree_mcp/server.py
```

不要把實際 password 放在 shell history、MCP tool arguments、README 或
source code。

## MCP tools

### 登入與讀取

- `timetree_login`、`timetree_logout`、`timetree_status`
- `timetree_keychain_status`
- `timetree_list_calendars`、`timetree_list_events`、`timetree_list_memos`
- `timetree_list_event_comments`
- `timetree_get_calendar_labels`、`timetree_get_calendar_members`、
  `timetree_get_calendar_virtual_members`

### 寫入

- `timetree_create_event`、`timetree_update_event`、`timetree_delete_event`
- `timetree_create_memo`、`timetree_update_memo`、`timetree_delete_memo`
- `timetree_add_event_comment`、`timetree_update_event_comment`、
  `timetree_delete_event_comment`
- `timetree_update_calendar_labels`

時間可傳 ISO-8601 字串或 Unix milliseconds；所有寫入工具都必須明確傳
`confirm: true`。成員邀請／移除 endpoint 尚未驗證，因此刻意未加入。

若某些舊 calendar 的 TimeTree sync endpoint 回傳 `-425`，events 查詢會跳過
該 calendar，並在 `skipped_calendars` 欄位回報，不會讓其他可讀 calendars
整批失敗。

### 背景監控

- `timetree_start_monitor`：啟動背景 polling，間隔 30–3600 秒。
- `timetree_monitor_status`：查看監控狀態、上次 polling、錯誤與通知數。
- `timetree_stop_monitor`：停止監控。

首次 polling 只建立 baseline；之後偵測到新增、修改、移除時，server 會送出
MCP `notifications/message`。監控依附於 MCP process，client 結束後也會停止。

## 專案結構

```text
timetree_mcp/              Python package 與 MCP server
scripts/                   本機設定輔助腳本
tests/                     standard-library unittest 測試
.github/workflows/ci.yml   Continuous integration
```

## 測試

```bash
python3 -m unittest discover -s tests -v
```

舊的 ego-browser transport 保留在 `timetree_mcp/browser.py` 作為相容備援，
但 server 預設不會載入它。

## License

本專案採用 [MIT License](LICENSE)。
