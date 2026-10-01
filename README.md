# TimeTree MCP

[![npm](https://img.shields.io/npm/v/timetree-mcp?color=blue)](https://www.npmjs.com/package/timetree-mcp)
[![CI](https://github.com/ap311036/timetree-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ap311036/timetree-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Node.js >=18](https://img.shields.io/badge/node-%3E%3D18-339933)](package.json)
[![Python >=3.10](https://img.shields.io/badge/python-%3E%3D3.10-3776AB)](pyproject.toml)

**讓你的 AI 助手讀懂，也安全地更新 TimeTree。**

不用再一邊聊天、一邊切回行事曆找資料。TimeTree MCP 是一個非官方的
[Model Context Protocol](https://modelcontextprotocol.io/) server，能把行程、
備忘、留言與行事曆資訊帶進支援 MCP 的 AI 助手；需要建立、修改或刪除內容
時，只有在你明確確認後才會真的寫入。

你可以直接這樣問：

- 「幫我整理這週的家庭行程，找出時間衝突。」
- 「下個月有哪些活動提到『牙醫』？」
- 「在家庭行事曆建立週六下午兩點的聚餐，先讓我確認內容。」
- 「監控這個行事曆，有新增、修改或移除時通知我。」

<p align="center">
  <img src="https://raw.githubusercontent.com/ap311036/timetree-mcp/main/docs/assets/timetree-mcp-demo.svg" alt="TimeTree MCP workflow showing local credential safety, MCP tools, and explicit confirmation for writes" width="960">
</p>

## 為什麼選擇 TimeTree MCP

| | 重點 |
|---|---|
| **對話中完成行事曆工作** | 查詢行程、搜尋活動、管理備忘與留言，不必離開正在使用的 AI 助手。 |
| **每次寫入都由你決定** | 所有新增、修改、刪除操作都必須明確傳入 `confirm: true`。 |
| **敏感資料留在本機** | credentials 不會出現在 MCP tool arguments；session cookie 與 CSRF token 只存在 process memory。 |
| **直接、輕量** | 預設由 Python direct client 透過 HTTPS 連線，不啟動瀏覽器，runtime 只使用 Python standard library。 |
| **掌握行事曆變化** | 可在背景 polling，並透過 MCP notifications 回報新增、修改與移除。 |

> [!IMPORTANT]
> TimeTree 官方 Connect App／API 已於
> [2023 年 12 月 22 日終止](https://timetreeapp.com/intl/en/newsroom/2023-07-23/connectapp-api)。
> 本專案使用 TimeTree Web 的非公開 endpoint，服務改版時可能失效。請自行
> 評估風險，並遵守 TimeTree 的條款與政策。

## 支援的操作

- 讀取 calendars、events、memos、comments、labels 與 members。
- 建立、更新、刪除 events 與 memos。
- 新增、更新、刪除 event comments。
- 以 merge 方式更新 calendar labels。
- 背景監控 calendar 變更並發送 MCP notifications。
- 所有寫入操作都要求明確傳入 `confirm: true`。
- 不把 credentials 放進 MCP tool arguments，authenticated session 只保存在記憶體。

## 快速開始

### 執行需求

- Python 3.10 或更新版本。
- TimeTree 帳號。
- 使用 NPM wrapper 時需要 Node.js 18 或更新版本。
- macOS 才能使用 Keychain helper；其他平台可使用環境變數或 password file。

### 最快方式：NPM wrapper

一行指令即可啟動 package 內附的 Python MCP server，並轉發 MCP 的
stdin/stdout：

```bash
npx --yes timetree-mcp
```

也可以全域安裝：

```bash
npm install --global timetree-mcp
timetree-mcp
```

接著完成下方的 [credentials 設定](#設定-credentials)，再把 server
[註冊到 Codex](#註冊到-codex)。

### 從原始碼安裝

```bash
git clone https://github.com/ap311036/timetree-mcp.git
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
  -- python3 /absolute/path/to/timetree-mcp/src/timetree_mcp/server.py
```

不要把實際 password 放在 shell history、MCP tool arguments、README 或
source code。

使用 NPM wrapper 註冊時，把 server command 改成：

```bash
codex mcp add timetree \
  --env TIMETREE_EMAIL=your-email@example.com \
  --env TIMETREE_PASSWORD_FILE=/absolute/path/to/timetree-password \
  -- npx --yes timetree-mcp
```

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

## 文件與 discoverability

- [Agent Guide](docs/agent-guide.md)：setup、tool workflow、安全規則與錯誤處理。
- [Usage Guide](docs/usage.md)：NPM client config、本機 checkout 與 monitoring。
- [Publishing](docs/publishing.md)：版本契約、NPM package、MCP Registry manifest 與 GitHub Actions。
- [`server.json`](server.json)：`io.github.ap311036/timetree-mcp` 的 MCP Registry metadata。
- [Companion skill](skills/timetree-mcp/SKILL.md)：給支援 skills 的 agent 使用的操作規範。

## 專案結構

```text
src/timetree_mcp/          Python package 與 MCP server
bin/                       NPM wrapper launcher
docs/                      Agent、發布與展示文件
skills/timetree-mcp/       Companion skill
server.json               MCP Registry manifest
scripts/                   本機設定輔助腳本
tests/                     standard-library unittest 測試
test/                      NPM wrapper 測試
.github/workflows/ci.yml   Continuous integration
```

## 測試

```bash
npm test
```

`npm test` 會依序執行 Node wrapper／promotion contract tests 與完整 Python
unittest suite。單獨執行 Python 測試：

```bash
env PYTHONPATH=src python3 -m unittest discover -s tests -v
```

舊的 ego-browser transport 保留在 `src/timetree_mcp/browser.py` 作為相容備援，
但 server 預設不會載入它。

## 發布

目前公開套件可直接使用：

```bash
npx --yes timetree-mcp
```

版本發布與 MCP Registry metadata 維護方式請見
[docs/publishing.md](docs/publishing.md)。

## License

本專案採用 [MIT License](LICENSE)。

### 同步完整性

事件讀取結果的 `sync_complete` 表示所選行事曆同步是否完成，
不表示 `limit` 篩選後的輸出涵蓋所有事件。游標可遞增或遞減；重複／無效游標
或 25 頁上限會在 `incomplete_calendars` 回報原因，並保留已讀事件。
上游 `-425` 會保留 `error_code`；沒有取得事件的失敗行事曆列於
`skipped_calendars`，整體 `sync_complete` 為 false。

此修正包含於 0.2.2；可用 `npx --yes timetree-mcp@0.2.2` 啟動。
本機原始碼也可用 `python3 src/timetree_mcp/server.py` 啟動。
