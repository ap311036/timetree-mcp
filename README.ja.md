# TimeTree MCP

**MCP アシスタントから TimeTree のカレンダーを読み取り、更新し、変更を監視できます。**

[English](README.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md)

[![npm](https://img.shields.io/npm/v/timetree-mcp?color=blue)](https://www.npmjs.com/package/timetree-mcp)
[![CI](https://github.com/ap311036/timetree-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ap311036/timetree-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Node.js >=18](https://img.shields.io/badge/node-%3E%3D18-339933)](package.json)
[![Python >=3.10](https://img.shields.io/badge/python-%3E%3D3.10-3776AB)](pyproject.toml)

TimeTree MCP は、カレンダー、予定、メモ、コメントの読み取りと更新に使える
[Model Context Protocol](https://modelcontextprotocol.io/) サーバーです。
ローカルで stdio を介して動作し、HTTPS で TimeTree に直接接続します。
カレンダーへの書き込みには `confirm: true` が必要です。
認証情報は macOS キーチェーンやローカルのパスワードファイルに保存できます。

```bash
npx --yes timetree-mcp@0.4.0
```

**npm を使う場合も Python 3.10 以上が必要です。** npm ランチャーには Node.js 18 以上も必要です。
カレンダーを操作する前に、認証情報と MCP クライアントを設定してください。
上のコマンドは stdio サーバーを起動するもので、対話型のチャット画面を開くものではありません。

**0.4.0 の新機能：** 繰り返し予定の期間照会とタイムゾーン診断が加わり、
全 29 ツールを利用できます。以前の npm 0.3.0 は 27 ツールで、これらの追加機能は含まれません。

[クイックスタート](#クイックスタート) · [クライアント設定と検証状況](docs/clients.md) ·
[再現可能なデモ](docs/demo.md) · [ツール一覧](#mcp-tools)

アシスタントへの依頼例：

- 「今週の家族の予定をまとめて、時間が重なる予定を教えて。」
- 「来月の予定から『歯医者』を含むものを探して。」
- 「土曜日の午後 2 時に家族の食事会を準備して。作成する前に内容を確認させて。」
- 「このセッションが開いている間、家族のカレンダーの変更を監視して。」

これらはプロンプトの例であり、TimeTree の実際の応答を収録したものではありません。
[デモ](docs/demo.md)に再現手順と検証範囲を記載しています。

## 主な機能

| 機能 | 内容 |
|---|---|
| 読み取りと検索 | カレンダー、期間・キーワードによる予定検索、メモ、コメント、ラベル、メンバー、バーチャルメンバー。 |
| 確認フラグ付きの書き込み | 予定・メモ・コメントの作成、更新、削除。ラベル名と色のマージ更新。 |
| 予定の詳細 | 時刻付き・終日の予定、タイムゾーン、メモ、場所、URL、ラベル、参加者、アラート、チェックリスト、バーチャル参加者、既存ファイル UUID への参照。 |
| 繰り返し予定の 1 回分を移動 | 対応する毎日・毎週の予定について、元の所要時間と対応メタデータを保ったまま 1 回分の時刻を変更。事前確認、リビジョン、操作 ID、ローカルの永続記録が必要です。 |
| 繰り返し予定の期間照会 | 対応する毎日・毎週・毎月・毎年のルールから、最大 93 日の指定期間に重なる回を読み取り専用で列挙。扱えない予定がある場合は結果が不完全であることを示します。 |
| タイムゾーン診断 | ローカル Python 環境でタイムゾーンデータを読み込めるか確認。カレンダーへの接続や書き込みは行いません。 |
| ツール引数に認証情報を含めない | 認証情報はプロセスの環境変数、パスワードファイル、macOS キーチェーンから取得。セッション Cookie と CSRF トークンはプロセスのメモリに保持します。 |
| 予定の変更を監視 | 30〜3600 秒間隔でポーリングし、予定の追加・更新・削除を MCP 通知として送信。 |
| 少ない依存関係 | Python の実行部分は標準ライブラリを使用。npm は同梱の Python サーバーを起動します。 |

`confirm: true` はサーバーが強制する API の条件です。
**アシスタントが人間の承認を得たことを保証するものではありません。**
クライアント側で書き込みの確認を設定し、対象のカレンダー、予定、タイムゾーン、変更範囲を確認してから承認してください。

認証情報はログインのために HTTPS で TimeTree に送信されます。カレンダーの内容は MCP クライアントに返され、
そのクライアントが利用する AI プロバイダーに処理される場合があります。
認証情報をローカルに保存しても、カレンダーの照会がローカルだけで完結するわけではありません。

> [!IMPORTANT]
> 本プロジェクトは TimeTree と提携していません。公式の Connect App/API は
> [2023 年 12 月 22 日に提供を終了しました](https://timetreeapp.com/intl/en/newsroom/2023-07-23/connectapp-api)。
> 本サーバーは、予告なく変更される可能性のある TimeTree Web の非公開エンドポイントを使用します。
> TimeTree の利用規約とポリシーに従って利用してください。

## クイックスタート

### 1. 必要な環境を確認する

- Python 3.10 以上と TimeTree アカウント。
- npm ランチャーを使う場合は Node.js 18 以上。
- ローカルの **stdio** MCP サーバーを起動できるクライアント。
- キーチェーンは macOS のみ対応。他のプラットフォームではパスワードファイルまたは環境変数を利用できます。
- 予定の 1 回分の移動には IANA タイムゾーンデータが必要です。Python はシステムのデータベース、または任意でインストールする `tzdata` パッケージを使用します。データがない場合は、固定オフセットに置き換えず処理を拒否します。

Codex、Claude Desktop、Cursor の設定例は[クライアントガイド](docs/clients.md)にあり、
それぞれの検証状況も記載しています。OpenAI の公式ドキュメントによると、ローカル MCP 設定を備える
ChatGPT デスクトップ版ではこの stdio コマンドを利用できますが、本プロジェクトではデスクトップ UI の実機検証を行っていません。
ChatGPT Web のカスタム app/plugin にはリモート MCP エンドポイントが必要であり、このパッケージでは提供していません。
クライアントごとの要件と制限はガイドを確認してください。

### 2. 認証情報を設定する

**パスワードファイル（各プラットフォーム共通）：** ローカルのテキストエディターで、TimeTree のパスワードだけを含むファイルを作成します。
macOS/Linux ではアクセス権を制限してください。

```bash
chmod 600 /absolute/path/to/timetree-password
```

クライアント設定から MCP プロセスに次の環境変数を渡します。

```text
TIMETREE_EMAIL=your-email@example.com
TIMETREE_PASSWORD_FILE=/absolute/path/to/timetree-password
```

Windows では適切なファイルアクセス権を設定してください。
パスワードをチャット、ツール引数、コミットするファイル、シェルのコマンド履歴に含めないでください。
`TIMETREE_PASSWORD` も利用できますが、パスワードファイルを使えばクライアントの JSON 設定にパスワードを保存せずに済みます。

**macOS キーチェーン：** ソースコードのチェックアウトに含まれる設定ヘルパーを利用できます。

```bash
git clone https://github.com/ap311036/timetree-mcp.git
cd timetree-mcp
python3 src/timetree_mcp/credentials.py set
python3 src/timetree_mcp/credentials.py status
```

パスワード入力は表示されません。status は情報が存在するかどうかだけを示し、値を返しません。
設定後は npm ランチャーがキーチェーンの項目を自動で読み取れるため、クライアント設定の認証用環境変数を省略できます。
保存した項目を削除するには、次のコマンドを実行します。

```bash
python3 src/timetree_mcp/credentials.py clear
```

パスワードは環境変数、パスワードファイル、キーチェーンの順に優先されます。
メールアドレスも、環境変数がキーチェーンより優先されます。

### 3. クライアントを接続する

Codex とパスワードファイルを使う場合：

```bash
codex mcp add timetree \
  --env TIMETREE_EMAIL=your-email@example.com \
  --env TIMETREE_PASSWORD_FILE=/absolute/path/to/timetree-password \
  -- npx --yes timetree-mcp@0.4.0
```

macOS キーチェーンを使う場合は 2 つの `--env` オプションを省略します。
すでに `timetree` の設定がある場合は、置き換える前に確認してください。
Claude Desktop、Cursor、実行ファイルのパス、トラブルシューティングは[クライアント設定](docs/clients.md)を参照してください。

アシスタントに `timetree_status`、続いて `timetree_list_calendars` を実行するよう依頼してください。
認証には設定済みの情報が使われます。会話にパスワードを貼り付けないでください。

### ソースコードからインストールする

npm の代わりに、チェックアウトしたディレクトリで次を実行できます。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
```

Windows の PowerShell では `.venv\Scripts\Activate.ps1` で仮想環境を有効にします。
インストールすると `timetree-mcp` と `timetree-credentials` コマンドが利用できます。
インストールせずに `python3 src/timetree_mcp/server.py` で起動することもできます。

## MCP tools

0.4.0 は、0.3.0 の 27 ツールに期間照会と診断の 2 ツールを加えた、全 29 ツールを提供します。

| 分類 | ツール |
|---|---|
| セッション | `timetree_login`、`timetree_logout`、`timetree_status`、`timetree_keychain_status` |
| 読み取り | `timetree_list_calendars`、`timetree_list_events`、`timetree_list_memos`、`timetree_list_event_comments` |
| 期間照会・診断 | `timetree_list_occurrences`、`timetree_timezone_status` |
| カレンダー情報 | `timetree_get_calendar_labels`、`timetree_get_calendar_members`、`timetree_get_calendar_virtual_members` |
| 予定 | `timetree_create_event`、`timetree_update_event`、`timetree_delete_event` |
| メモ | `timetree_create_memo`、`timetree_update_memo`、`timetree_delete_memo` |
| コメント | `timetree_add_event_comment`、`timetree_update_event_comment`、`timetree_delete_event_comment` |
| ラベル | `timetree_update_calendar_labels` |
| 1 回分の移動 | `timetree_get_occurrence_for_edit`、`timetree_move_occurrence`、`timetree_occurrence_operation_status` |
| 監視 | `timetree_start_monitor`、`timetree_monitor_status`、`timetree_stop_monitor` |

予定、メモ、コメント、ラベルへの書き込みにはすべて `confirm: true` が必要です。
一般の予定の時刻には ISO-8601 文字列または Unix ミリ秒を指定できます。
1 回分の移動には、秒と数値 UTC オフセットを明示した時刻を使用してください。
更新や削除の前にカレンダーを一覧表示し、読み取り結果の予定 ID を確認してください。
ラベルのマージ更新では、指定していないラベルを保持します。
全 29 ツールの引数、機能、対応範囲は[ツールリファレンス](docs/tools.md)を参照してください。

### 繰り返し予定の期間照会とタイムゾーン診断（0.4.0）

`timetree_list_occurrences` は、指定したカレンダーの読み取り結果から、期間内に重なる
時刻付きの予定を列挙します。元のシリーズ、移動済みの子イベント、通常の予定を区別し、
除外と親子関係を保守的に確認し、曖昧なグループは結果から除いて不完全と報告します。
各子イベントと元の回の 1 対 1 の対応を保証するものではありません。期間は開始を含み、終了を含まず、
最大 93 × 24 時間です。対応する毎日・毎週のルールに加え、毎月・毎年の一部のルールを照会できます。
月末や閏日など存在しない日付は、対応ルールの定義に従って扱います。

対応外のルール、時刻、親子関係などがある場合は、不完全な結果であることを返します。
不完全な結果や件数上限で切られた一覧だけから、予定がないことや空き時間を断定しないでください。
終日の予定、任意の繰り返しルール、すべてのタイムゾーンを扱えるわけではありません。
この追加機能によって、毎月・毎年の予定の 1 回分の変更が可能になるわけではありません。
書き込み側の対応範囲は引き続き毎日・毎週に限ります。

`timetree_timezone_status` は、選択した Python 環境で IANA タイムゾーンを読み込めるかを
診断します。認証やカレンダーへのアクセスは不要です。タイムゾーンデータがない場合は、
MCP サーバーと同じ Python 環境に `tzdata` を導入する必要があります。
データを読み込めても、そのタイムゾーンが予定の変更機能に対応しているとは限りません。
照会と診断の引数・応答・制限は[期間照会ガイド](docs/occurrence-queries.md)、
変更の対応範囲は[繰り返し予定ガイド](docs/recurring-events.md)を確認してください。

0.4.0 では、繰り返し予定の照会・編集の新たな検証時にタイムゾーンデータを
改めて読み込み、シリーズ開始日の曜日を含まない毎週の `BYDAY` ルールを拒否します。
記録済み操作の結果の再取得は引き続きオフラインで行い、タイムゾーンデータを必要としません。
これらの追加保護は npm 0.3.0 には含まれません。

### 繰り返し予定の 1 回分を移動する

**0.3.0 の新機能（npm 0.2.4 には含まれません）：**
対応するタイムゾーンの毎日・毎週の予定について、1 回分の時刻を変更できます。
元の所要時間と対応するメタデータを保持します。対応タイムゾーンは `Asia/Taipei`、
`Asia/Tokyo`、`Asia/Kolkata`、`America/New_York`、`Europe/London` です。
開始と終了は同じ IANA タイムゾーンである必要があり、日時は 1980 年以降、
終日ではない予定、所要時間は 0 時間より長く 24 時間以内に限ります。

入力は `2030-01-07T09:00:00+09:00` のように秒と数値オフセットを明示します。
オフセットはその日時のタイムゾーンと一致する必要があります。夏時間の切り替えで存在しない時刻や
2 通りに解釈できる時刻、予定の途中でオフセットが変わる場合、安全に検証できない繰り返し、
開始・終了のタイムゾーンが異なる場合、タイムゾーンデータがない場合は拒否します。
安全に検証できる場合は、夏時間をまたいでも現地の同じ時刻に繰り返す予定を扱えます。
New York と London では、繰り返し予定の開始日から対象の回までを最大 3660 暦日まで検証します。
この上限を超える場合も拒否します。
対応外のルールや重要なメタデータを黙って削除して処理することはありません。

事前確認では元の回の正確な時刻と、既存の親子イベントの関連を検証します。
事前確認と成功結果には `timezone` も返されます。
移動には `confirm: true`、返されたリビジョン、同じ論理操作に対して固定した操作 ID が必要です。
ローカルの永続ジャーナルが中断後の重複送信を防ぎますが、子イベントの作成と元の回の除外は
2 つの非アトミックな書き込みです。上流側に compare-and-swap はなく、別クライアントからの
同時編集を完全には防げません。

結果が不明な場合は再送せず、`timetree_occurrence_operation_status` でローカルの記録を確認してください。
このツールは現在のカレンダー状態を取得せず、再開、再試行、ロック解除も行いません。
新しい操作 ID や別の保存先に切り替えて保護を回避しないでください。
使用前に[対応範囲と復旧上の制限](docs/recurring-events.md)を確認してください。

新機能は合成データによるテストと、認証なしで取得した公開フロントエンドの調査で検証しています。
実際のカレンダーへの書き込みや、クライアント UI を通した一連の操作は、この実装の検証では行っていません。

1 回分の削除や「この回以降」の編集には対応していません。
1 回分だけの変更を、一般の予定更新・削除で代用しないでください。
`timetree_list_events` は繰り返しルールを展開しないため、期間指定の検索では後続の回が見つからないことがあります。
一般の予定ツールには繰り返しルールの配列を渡せますが、任意のルールや編集範囲に対応することを保証しません。
ファイルのアップロード、カレンダー管理、メンバーの招待・削除ツールはありません。
すべての引数、任意の項目、環境変数、未対応の操作は[ツールリファレンス](docs/tools.md)を参照してください。

### 監視の制限

最初のポーリングで基準状態を作り、以降の変更を MCP `notifications/message` として送信します。
通知が表示されるかどうかはクライアントによります。ポーリング方式であり、即時プッシュ配信ではありません。
メールやスマートフォンの通知も送信しません。
MCP プロセスが終了すると監視も停止します。常時動作するクラウドサービスではなく、
プロセスの再起動後は監視を再度開始する必要があります。
`timetree_monitor_status` で前回のポーリング、エラー、通知数を確認できます。
一部しか取得できなかった状態が削除として見えることがあるため、新しい読み取りで確認してください。

### 読み取りの完全性

`sync_complete` は、選択したカレンダーの同期が完了したかを示します。
`limit` で絞った結果にすべての予定が含まれることを示すものではありません。
重複・不正なカーソルや 25 ページの上限は `incomplete_calendars` に報告し、取得済みの予定は保持します。
上流の `-425` エラーでは `error_code` を保持します。予定を 1 件も取得できなかったカレンダーは
`skipped_calendars` に含まれ、全体の `sync_complete` は false になります。

予定の期間検索は **UTC の午前 0 時**を境界とし、`start_date` を含み、`end_date` を含みません。
予定のタイムゾーンにおける日付境界ではありません。キーワード検索の対象はタイトル、メモ、場所、
カレンダー名・ID、返されたラベル名です。メモの読み取りには別の `updated_after` フィルターがありますが、
現在は同期の完全性を示す項目を返しません。

カレンダー一覧と既定の予定同期は、有効なカレンダーだけを対象とします。
`deactivated_at` が未指定、`null`、数値の `0` の場合は有効、0 以外の数値の場合は無効です。
不正な型はエラーになり、無効なカレンダーを明示的に指定した場合も同期前に拒否します。
直接 HTTPS 接続と旧ブラウザー接続の両方に同じ規則を適用します。
予定を要約したり、削除されたように見える項目を判断したりする際は、不完全な読み取りを部分的な情報として扱ってください。

## ドキュメントと開発

- [クライアント設定](docs/clients.md)：設定例と検証状況。
- [デモ](docs/demo.md)：再現可能な操作と検証範囲。
- [使い方](docs/usage.md)：ローカル開発と監視。
- [エージェントガイド](docs/agent-guide.md)：ツールの利用手順とエラー処理。
- [ツールリファレンス](docs/tools.md)：全 29 ツールの引数、機能、制限と設定用環境変数。
- [期間照会とタイムゾーン診断](docs/occurrence-queries.md)：0.4.0 で追加した読み取り機能の対応範囲と例。
- [隔離したカレンダーでの検証](docs/isolated-calendar-testing.md)：実環境での検証の前提、現在の障害、合成データのケース。
- [繰り返し予定の 1 回分の移動](docs/recurring-events.md)：対応範囲、タイムゾーン、ジャーナル、復旧上の制限。
- [補助スキル](skills/timetree-mcp/SKILL.md)：スキルに対応したエージェント向けの操作規範。
- [公開手順](docs/publishing.md)：npm、バージョン確認、MCP Registry メタデータ、GitHub Actions。
- [検索での表示](docs/discoverability.md)：正確なプロジェクト説明と検索結果の表示上の制限。
- [`server.json`](server.json)：`io.github.ap311036/timetree-mcp` のマニフェスト。
- [セキュリティポリシー](SECURITY.md) · [貢献ガイド](CONTRIBUTING.md)。

```bash
npm test
npm run pack:check
```

`npm test` は Node ランチャー・メタデータのテストと Python の unittest を実行します。
Python のテストだけを実行することもできます。

```bash
env PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Python サーバーは `src/timetree_mcp/`、npm ランチャーは `bin/`、テストは `tests/` と `test/` にあります。
旧 ego-browser 接続は互換性のため `src/timetree_mcp/browser.py` に残しています。
サーバーの既定の接続方法は直接 HTTPS です。

## ライセンス

[MIT](LICENSE)。
