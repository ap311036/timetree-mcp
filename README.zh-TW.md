# TimeTree MCP

**在 MCP 助手中讀取、更新與監控你的 TimeTree 行事曆。**

[English](README.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md)

[![npm](https://img.shields.io/npm/v/timetree-mcp?color=blue)](https://www.npmjs.com/package/timetree-mcp)
[![CI](https://github.com/ap311036/timetree-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ap311036/timetree-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Node.js >=18](https://img.shields.io/badge/node-%3E%3D18-339933)](package.json)
[![Python >=3.10](https://img.shields.io/badge/python-%3E%3D3.10-3776AB)](pyproject.toml)
[![MCP Badge](https://lobehub.com/badge/mcp/ap311036-timetree-mcp)](https://lobehub.com/mcp/ap311036-timetree-mcp)

TimeTree MCP 是 [Model Context Protocol](https://modelcontextprotocol.io/)
server，可搜尋行事曆、操作行程與備忘錄、管理留言及監控變更。它在本機透過 stdio 執行，直接以 HTTPS
連線到 TimeTree；所有行事曆寫入都要求 `confirm: true`。認證資料可保存在 macOS
Keychain 或本機密碼檔。

```bash
npx --yes timetree-mcp@0.6.0
```

**即使使用 npm，仍須安裝 Python 3.10+。** npm wrapper 另需 Node.js 18+。
操作行事曆前，請先設定認證資料與 MCP client；上述指令啟動的是 stdio server，
不是互動式聊天介面。

**0.6.0** 新增唯讀檢視提示詞及靜態安全資源，維持 **29 個工具**。
請見 [0.6.0 發版說明](docs/release-0.6.0.md)。

**0.5.0** 維持 **29 個工具**，改善認證來源選擇、HTTPS 登入、同步完整性與監控狀態。
唯讀的重複場次查詢與時區資料診斷於 0.4.0 加入。
支援規則與驗證限制請見[場次查詢與時區診斷](#場次查詢與時區診斷)。
升級相容性變更請見 [0.5.0 發版說明](docs/release-0.5.0.md)。

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
| 行程詳細資料 | 非全天／全天行程、筆記、地點、網址、標籤、參與者、提醒、核取清單、虛擬參與者，以及既有檔案 UUID 引用。 |
| 移動單次重複行程 | 預檢並移動台北、東京、加爾各答、紐約或倫敦時區的每日／每週場次，具嚴格時區驗證與本機持久操作紀錄。 |
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
- 單次移動需要 IANA 時區資料庫。Python 使用系統資料庫或選用的 `tzdata` 套件；
  缺少資料時會拒絕操作，不會退回固定 UTC 偏移。

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

請提供完整認證組合：`TIMETREE_EMAIL` 搭配 `TIMETREE_PASSWORD` 或
`TIMETREE_PASSWORD_FILE`；已設定的密碼優先於密碼檔。使用 Keychain 時，請移除這三個認證環境變數。

**自 0.5.0 起：**明確設定的認證必須成對且非空，不會從 Keychain 補入缺少的欄位；
完整的明確認證也不會存取 Keychain。`timetree_status` 僅回報選用來源與是否已設定，
不讀取密碼檔內容或 Keychain 秘密；`credentials_configured` 不代表登入一定成功。
這些變更未包含在 npm 0.4.0，詳見[認證選擇與狀態](docs/clients.md#credential-resolution-and-status)。

**0.5.0** 讓一般 reader 與受限 E2E runner 共用同一套正式認證與 HTTPS 實作：
一次登入 PUT，接著一次 `/signin` CSRF GET。只有精確驗證的日曆路徑 redirect，才允許額外一次固定的
`/calendars` GET；不接受 redirect 的 body、cookie 或 token，第二次 redirect 會失敗。
取得有效的 2xx CSRF 回應後才標示登入成功；失敗或取消會清除本機 session 狀態。
預設 transport 現在會明確拒絕啟用或無法判定的 proxy、無效 HTTPS origin 與其他 redirect，
並限制回應及 cookie 大小／數量。這會改變依賴 urllib 隱式 proxy 的環境相容性，
npm 0.4.0 則維持原狀。詳見[共用認證與傳輸](docs/clients.md#shared-authentication-and-transport)。

共用認證與單次日曆頁初始化的離線測試及獨立審查已完成，在所審查範圍內沒有剩餘阻擋。
帳號擁有者回報真實 stdio 流程已完成，**8/8 項指定測試資料核對通過**。
發版前檢查時，程式 hash 與已審查版本一致，核准計畫的 hash 也未變；但執行報告沒有版本／原始碼／計畫指紋，
因此無法事後獨立確認當時實際載入的檔案就是發版產物。
這項唯讀結果不涵蓋 npm 安裝與原始入口、GUI client、其他認證來源探索、真實寫入或通知收件。
六筆測試資料之後已刪除並核對不存在，已完成的計畫不可重跑；詳見[回報結果與範圍](docs/e2e-runner.md)。

### 3. 連接 client

Codex 搭配密碼檔的設定：

```bash
codex mcp add timetree \
  --env TIMETREE_EMAIL=your-email@example.com \
  --env TIMETREE_PASSWORD_FILE=/absolute/path/to/timetree-password \
  -- npx --yes timetree-mcp@0.6.0
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

## MCP 提示詞與資源（0.6.0）

**0.6.0** 新增一個提示詞及一個資源，**29 個工具維持不變**。
需要支援 MCP prompts 或 resources 的 client；協定相容性請見
[0.6.0 發版說明](docs/release-0.6.0.md)。

- `timetree_review_schedule` 回傳單一所選行事曆的唯讀檢視模板。
  `calendar_id` 必須使用 `timetree_list_calendars` 回傳的數字 `id`，不能使用網頁網址短碼。
  `start_date` 含起日、`end_date` 不含迄日，皆以 **UTC 午夜**為邊界，範圍最多 93 天；
  這些邊界不是行事曆所在時區的本地日界。
  模板引導助手檢查場次查詢的完整性並回報不確定之處；不完整的結果不能證明時段有空。
  取得模板本身不會登入、查詢 TimeTree 或寫入行事曆。
- `timetree://guides/safe-usage` 提供可由 client 附加為上下文的靜態
  **TimeTree safe usage guide**。內容不含認證資料或行程記錄，讀取時不會存取
  認證、行事曆或本機檔案。

是否使用模板或附加指南由 client 控制。助手呼叫行事曆工具前，請核對所選的
行事曆與日期範圍。參數、UTC 範例、協定支援與驗證限制詳見
[提示詞與靜態指南](docs/prompts-and-resources.md)。

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
| 單次移動 | `timetree_get_occurrence_for_edit`、`timetree_move_occurrence`、`timetree_occurrence_operation_status` |
| 監控 | `timetree_start_monitor`、`timetree_monitor_status`、`timetree_stop_monitor` |
| 場次查詢與診斷（0.4.0） | `timetree_list_occurrences`、`timetree_timezone_status` |

所有行程、備忘錄、留言與標籤寫入都必須傳入 `confirm: true`。
一般行程時間支援 ISO-8601 字串或 Unix milliseconds；單次移動須精確到秒，且明確指定與系列時區相符的數字 UTC 偏移。
更新或刪除前，先列出行事曆並核對
讀取結果中的行程 ID。標籤採合併更新，保留未指定的標籤。

空的 `attendees` 或 `alerts` 不代表其他日曆成員不會收到 TimeTree 通知。
新增留言及單次移動會送出 `silent`，但尚未驗證它對通知的實際影響。
一般行程建立／更新工具不提供受支援的 `silent` 參數。詳見[通知限制](docs/tools.md#events)。

**0.5.0 修正：**行程建立／更新會拒絕先前被忽略的 `silent` 與 `content`；
新增留言會拒絕 `null`、`"false"` 等非布林 `silent` 值。
省略留言的 `silent` 仍預設為 true，也可傳入 JSON `true`／`false`。
此驗證修正尚未包含在 npm 0.4.0；詳見[相容性說明](docs/tools.md#memos-comments-and-labels)。

**0.3.0 新功能（0.2.4 未包含）：**可移動 `Asia/Taipei`、`Asia/Tokyo`、`Asia/Kolkata`、
`America/New_York` 或 `Europe/London` 時區、非全天、每日／每週重複行程的單次場次，
保留原時長及支援的資料欄位。預檢會確認原場次時間與父子事件連結；
寫入須帶 `confirm: true`、預檢 revision 與固定操作 ID。本機持久紀錄會阻擋中斷後重複送出，
但建立子事件及排除原場次仍是兩個非原子寫入。使用前請讀[支援範圍與恢復限制](docs/recurring-events.md)。

起訖須使用相同且受支援的 IANA 時區，日期須在 1980 年起，時長最多 24 小時。
缺少時區資料、偏移不符、重複或不存在的本地時間，以及事件區間跨越 UTC 偏移變更，
都會拒絕操作。可安全判定的本地時間重複規則可跨越夏令時間變更；紐約／倫敦最多
驗證距系列起點 3660 個本地日，遇到中間場次無法安全判定也會拒絕。
預檢與成功結果都回傳系列 `timezone`。這些規則以模擬資料測試；
本次未在真實行事曆執行多時區寫入，也未進行 client UI 完整實測。

尚未提供單次刪除或「這次及之後」修改。不要用一般行程更新／刪除代替單次操作。
`timetree_list_events` 仍不展開重複規則，因此日期範圍查詢可能漏掉後續場次。
一般行程工具可傳送原始 recurrence 陣列，但不保證任意重複規則或編輯範圍都正確受支援。
未提供檔案上傳、行事曆管理或成員邀請／移除工具。
全部 29 個工具的參數、選用欄位、環境設定及未支援操作，
見[完整工具參考](docs/tools.md)。

### 場次查詢與時區診斷

`timetree_list_occurrences` 可在最長 93 天的範圍內，將支援的非全天重複系列展開為個別場次，
並納入單一行程及已分離的場次。支援五個時區中的部分每日、每週、每月與每年規則；
若有未支援的規則或無法安全判定的時區情況，會明確標示結果不完整。
每月與每年規則目前**僅供查詢**，單次修改仍限每日／每週。
不完整或遭截斷的結果不能用來保證某時段有空，詳見[查詢範圍與範例](docs/occurrence-queries.md)。

`timetree_timezone_status` 會檢查執行 MCP server 的 Python 環境是否具備時區資料，
不需登入，也不讀寫行事曆；可區分支援單次修改的時區及其他被查詢的時區，
並在資料缺失時提供安裝指引。診斷通過不代表已驗證真實行事曆行為，
也不會開放原本未支援的修改範圍。

0.4.0 也讓新的場次查詢／單次修改驗證重新載入時區資料，並拒絕 `BYDAY`
未包含系列起始日星期的每週規則。已記錄操作的結果重播仍在離線完成，不需時區資料。
這些新增保護尚未包含在 npm 0.3.0。

### 監控限制

首次輪詢建立基準；後續變更以 MCP `notifications/message` 傳送，是否顯示取決於 client。
此功能採輪詢，不是即時推播，也不會寄送 email 或手機通知。
MCP 程序關閉後監控便會停止，不是持續運作的雲端服務；重新啟動程序後須再次啟動監控。
使用 `timetree_monitor_status` 查看上次輪詢、錯誤與通知計數。
npm 0.4.0 的部分同步結果可能看起來像行程被移除，請以重新讀取確認疑似刪除。

**自 0.5.0 起：**只有完整且驗證通過的快照能推進基準。
變更批次保存在有容量限制的記憶體佇列；已知未送出的批次可重試，
傳輸結果不確定的批次則保持阻擋，不會自動重送。狀態分別回報輪詢嘗試、完整檢查、
待處理批次與本機傳輸寫入成功次數；寫入成功不代表 client 顯示或使用者已收到。
同一程序內停止／啟動會保留狀態，但程序重啟會遺失狀態，也不會釐清先前不確定結果。
停止逾時會保留原工作執行緒並阻擋第二個輪詢器。詳見[監控狀態與交付](docs/monitoring.md)。
這些變更未包含在 npm 0.4.0。

### 讀取完整性

`sync_complete` 表示所選行事曆是否同步完成，不表示經過 `limit` 篩選後的結果涵蓋全部行程。
重複或無效游標，以及 25 頁上限，會在 `incomplete_calendars` 回報，並保留已讀取的行程。
上游 `-425` 錯誤會保留 `error_code`；未取得任何行程的失敗行事曆列於
`skipped_calendars`，整體 `sync_complete` 為 false。

行程日期範圍以 UTC 午夜為界，包含 `start_date`、不包含 `end_date`，並非事件時區的本地日界線。
關鍵字搜尋涵蓋標題、筆記、地點、行事曆名稱／ID，以及回傳的標籤名稱。
備忘錄另有 `updated_after` 篩選。npm 0.4.0 不回傳備忘錄同步完整性欄位；
**0.5.0** 提供 `sync_complete` 與略過／不完整行事曆診斷。
原始識別資料不正確、重複或事件分頁無效時，也會標示為不完整並保留先前已接受的資料，
不會聲稱已完整讀取；嚴格的重複場次讀取則會拒絕整份不完整來源。

行事曆列表與預設行程同步僅處理啟用中的行事曆：`deactivated_at` 缺少、`null` 或數字 `0`
視為啟用，非零數字視為停用。無效型別會明確報錯；明確指定停用行事曆也會在同步前被拒絕。
直接 HTTPS 與舊 browser reader 採用相同規則。整理行程或判讀疑似移除的項目時，
應將不完整的讀取視為部分資料。

## 文件與開發

- [Client 設定](docs/clients.md)：設定範例與驗證狀態。
- [示範](docs/demo.md)：可重現的操作及驗證界線。
- [Usage guide](docs/usage.md)：本機開發與監控。
- [監控狀態與交付](docs/monitoring.md)：0.5.0 的完整讀取驗證與待處理批次行為。
- [本機 stdio 驗證](docs/e2e-runner.md)：擁有者回報 8/8 項唯讀核對通過、原始碼歸因限制與暫時認證流程。
- [完整工具參考](docs/tools.md)：全部 29 個工具、支援欄位、預設值與環境設定。
- [場次查詢](docs/occurrence-queries.md)：0.4.0 新增的重複規則展開與時區診斷。
- [隔離日曆測試](docs/isolated-calendar-testing.md)：真實測試前提、目前阻擋與合成案例。
- [Agent guide](docs/agent-guide.md)：工具流程與錯誤處理。
- [Companion skill](skills/timetree-mcp/SKILL.md)：供支援 skills 的 agent 使用的操作規範。
- [Publishing](docs/publishing.md)：npm、版本檢查、MCP Registry metadata 與 GitHub Actions。
- [搜尋曝光](docs/discoverability.md)：準確的專案描述與搜尋外觀限制。
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
