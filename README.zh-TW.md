# TimeTree MCP

**在 MCP 助手中讀取、更新與監控你的 TimeTree 行事曆。**

[English](README.md) · [繁體中文](README.zh-TW.md)

[![npm](https://img.shields.io/npm/v/timetree-mcp?color=blue)](https://www.npmjs.com/package/timetree-mcp)
[![CI](https://github.com/ap311036/timetree-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ap311036/timetree-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Node.js >=18](https://img.shields.io/badge/node-%3E%3D18-339933)](package.json)
[![Python >=3.10](https://img.shields.io/badge/python-%3E%3D3.10-3776AB)](pyproject.toml)

TimeTree MCP 是非官方的 [Model Context Protocol](https://modelcontextprotocol.io/)
server，可操作行事曆、行程、備忘錄與留言。它在本機透過 stdio 執行，直接以 HTTPS
連線到 TimeTree；所有行事曆寫入都要求 `confirm: true`。認證資料可保存在 macOS
Keychain 或本機密碼檔。

```bash
npx --yes timetree-mcp
```

**即使使用 npm，仍須安裝 Python 3.10+。** npm wrapper 另需 Node.js 18+。
操作行事曆前，請先設定認證資料與 MCP client；上述指令啟動的是 stdio server，
不是互動式聊天介面。

[快速開始](#快速開始) · [Client 設定與驗證狀態](docs/clients.md) ·
[可重現示範](docs/demo.md) · [工具清單](#mcp-tools)

可以這樣請助手幫忙：

- 「整理這週的家庭行程，找出時間重疊的活動。」
- 「找出下個月提到『牙醫』的行程。」
- 「準備週六下午兩點的家庭聚餐，建立前先讓我確認內容。」
- 「在這次連線保持開啟時，監控家庭行事曆的變更。」

以上是提示詞範例，並非擷取的 TimeTree 實際回覆。
[示範文件](docs/demo.md)提供可重現的步驟，並說明驗證涵蓋的範圍。

## 支援功能

| 功能 | 說明 |
|---|---|
| 讀取與搜尋 | 行事曆、日期範圍及關鍵字行程查詢、備忘錄、留言、標籤、成員與虛擬成員。 |
| 寫入確認旗標 | 建立、更新、刪除行程、備忘錄與留言；合併更新標籤名稱及顏色。 |
| 認證資料不進入工具參數 | 從本機程序環境、密碼檔或 macOS Keychain 取得認證；session cookie 與 CSRF token 保存在程序記憶體。 |
| 監控行程變更 | 每 30–3600 秒輪詢，以 MCP notifications 回報新增、修改或移除的行程。 |
| 輕量執行 | Python runtime 僅使用標準函式庫；npm 負責啟動套件內附的 Python server。 |

`confirm: true` 是程式強制檢查的 API 門檻，**不能證明助手已向使用者取得確認**。
請在 client 設定寫入審查，核對目標行事曆、行程、時區與變更範圍，再核准操作。

認證資料會透過 HTTPS 傳送至 TimeTree 以登入。行事曆內容會回傳給 MCP client，
也可能由它使用的 AI 供應商處理；將認證資料存在本機，不代表行事曆查詢只在本機處理。

> [!IMPORTANT]
> 本專案與 TimeTree 無隸屬關係。官方 Connect App／API
> [已於 2023 年 12 月 22 日終止](https://timetreeapp.com/intl/en/newsroom/2023-07-23/connectapp-api)。
> 本專案使用可能隨時變動的 TimeTree Web 非公開端點。
> 請遵守 TimeTree 的條款與政策。

## 快速開始

### 1. 確認需求

- Python 3.10 或更新版本，以及 TimeTree 帳號。
- 使用 npm launcher 時，另需 Node.js 18 或更新版本。
- 能啟動本機 **stdio** MCP server 的 client。
- Keychain 僅支援 macOS；其他平台可使用密碼檔或環境變數。

Codex、Claude Desktop 與 Cursor 的設定範例見
[client 指南](docs/clients.md)，其中另列各項驗證狀態。
依 OpenAI 官方文件，提供本機 MCP 設定的 ChatGPT 桌面版本可使用此 stdio 指令，
但本專案尚未完成桌面 UI 實測。ChatGPT 網頁版自訂 app/plugin 需要遠端 MCP endpoint，
本套件尚未提供；各客戶端的需求與限制詳見指南。

### 2. 設定認證資料

**密碼檔（跨平台）：** 使用本機文字編輯器建立只包含 TimeTree 密碼的檔案。
在 macOS／Linux 限制檔案權限：

```bash
chmod 600 /absolute/path/to/timetree-password
```

透過 client 設定，將以下環境變數交給 MCP 程序：

```text
TIMETREE_EMAIL=your-email@example.com
TIMETREE_PASSWORD_FILE=/absolute/path/to/timetree-password
```

Windows 請設定適當的檔案存取權限。不要把密碼放進聊天、工具參數、版本庫檔案或
shell 指令歷史。也支援 `TIMETREE_PASSWORD`，但使用密碼檔可避免將密碼存入 client JSON。

**macOS Keychain：** 可從原始碼 checkout 執行設定工具：

```bash
git clone https://github.com/ap311036/timetree-mcp.git
cd timetree-mcp
python3 src/timetree_mcp/credentials.py set
python3 src/timetree_mcp/credentials.py status
```

密碼輸入會隱藏；status 僅顯示資料是否存在，不回傳值。
設定後 npm launcher 會自動讀取這些 Keychain 項目；client 設定可省略認證環境變數。
若要移除項目：

```bash
python3 src/timetree_mcp/credentials.py clear
```

密碼依序優先使用環境變數、密碼檔，再使用 Keychain。
Email 環境變數也優先於 Keychain。

### 3. 連接 client

Codex 搭配密碼檔的設定：

```bash
codex mcp add timetree \
  --env TIMETREE_EMAIL=your-email@example.com \
  --env TIMETREE_PASSWORD_FILE=/absolute/path/to/timetree-password \
  -- npx --yes timetree-mcp
```

使用 macOS Keychain 時，省略兩個 `--env` 選項。若已有 `timetree` 設定，先檢查再替換。
Claude Desktop、Cursor、執行檔路徑及問題排查方式，請見 [client 設定](docs/clients.md)。

請助手先執行 `timetree_status`，再執行 `timetree_list_calendars`。
登入使用預先設定的認證資料，不需要也不應將密碼貼進對話。

### 從原始碼安裝

除了 npm，也可在 checkout 中執行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
```

Windows PowerShell 請改用 `.venv\Scripts\Activate.ps1` 啟用虛擬環境。
安裝後會提供 `timetree-mcp` 與 `timetree-credentials` 指令。
也可不安裝，直接執行 `python3 src/timetree_mcp/server.py`。

## MCP tools

| 分類 | 工具 |
|---|---|
| 連線狀態 | `timetree_login`、`timetree_logout`、`timetree_status`、`timetree_keychain_status` |
| 讀取 | `timetree_list_calendars`、`timetree_list_events`、`timetree_list_memos`、`timetree_list_event_comments` |
| 行事曆資料 | `timetree_get_calendar_labels`、`timetree_get_calendar_members`、`timetree_get_calendar_virtual_members` |
| 行程 | `timetree_create_event`、`timetree_update_event`、`timetree_delete_event` |
| 備忘錄 | `timetree_create_memo`、`timetree_update_memo`、`timetree_delete_memo` |
| 留言 | `timetree_add_event_comment`、`timetree_update_event_comment`、`timetree_delete_event_comment` |
| 標籤 | `timetree_update_calendar_labels` |
| 監控 | `timetree_start_monitor`、`timetree_monitor_status`、`timetree_stop_monitor` |

所有行程、備忘錄、留言與標籤寫入都必須傳入 `confirm: true`。
時間支援 ISO-8601 字串或 Unix milliseconds。更新或刪除前，先列出行事曆並核對
讀取結果中的行程 ID。標籤採合併更新，保留未指定的標籤。

重複行程可傳入 `recurrences` 欄位，但尚無經驗證的工具可僅修改或刪除
**單次場次**或**這次及之後的場次**。不要用一般行程更新／刪除代替這些限定範圍的操作。
讀取時也不會將重複規則展開為各個未來場次，因此日期範圍查詢可能漏掉後續重複場次。
詳見[重複行程限制](docs/recurring-events.md)。成員邀請及移除端點也尚未驗證，
因此未提供相關工具。

### 監控限制

首次輪詢建立基準；後續變更以 MCP `notifications/message` 傳送，是否顯示取決於 client。
此功能採輪詢，不是即時推播，也不會寄送 email 或手機通知。
MCP 程序關閉後監控便會停止，不是持續運作的雲端服務；重新啟動程序後須再次啟動監控。
使用 `timetree_monitor_status` 查看上次輪詢、錯誤與通知計數。

### 讀取完整性

`sync_complete` 表示所選行事曆是否同步完成，不表示經過 `limit` 篩選後的結果涵蓋全部行程。
重複或無效游標，以及 25 頁上限，會在 `incomplete_calendars` 回報，並保留已讀取的行程。
上游 `-425` 錯誤會保留 `error_code`；未取得任何行程的失敗行事曆列於
`skipped_calendars`，整體 `sync_complete` 為 false。

行事曆列表與預設行程同步僅處理啟用中的行事曆：`deactivated_at` 缺少、`null` 或數字 `0`
視為啟用，非零數字視為停用。無效型別會明確報錯；明確指定停用行事曆也會在同步前被拒絕。
直接 HTTPS 與舊 browser reader 採用相同規則。整理行程或判讀疑似移除的項目時，
應將不完整的讀取視為部分資料。

## 文件與開發

- [Client 設定](docs/clients.md)：設定範例與驗證狀態。
- [示範](docs/demo.md)：可重現的操作及驗證界線。
- [Usage guide](docs/usage.md)：本機開發與監控。
- [Agent guide](docs/agent-guide.md)：工具流程與錯誤處理。
- [Companion skill](skills/timetree-mcp/SKILL.md)：供支援 skills 的 agent 使用的操作規範。
- [Publishing](docs/publishing.md)：npm、版本檢查、MCP Registry metadata 與 GitHub Actions。
- [`server.json`](server.json)：`io.github.ap311036/timetree-mcp` 的 manifest。
- [安全政策](SECURITY.md) · [貢獻指南](CONTRIBUTING.md)。

```bash
npm test
npm run pack:check
```

`npm test` 會執行 Node wrapper／metadata 測試與 Python unittest suite。
也可單獨執行 Python 測試：

```bash
env PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Python server 位於 `src/timetree_mcp/`，npm launcher 位於 `bin/`，測試位於 `tests/` 與 `test/`。
舊 ego-browser transport 保留在 `src/timetree_mcp/browser.py` 作為相容備援；
server 預設使用直接 HTTPS。

## License

[MIT](LICENSE)。
