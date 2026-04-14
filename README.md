# japan-ir-search

AIエージェントから日本の企業IR開示書類をテキスト検索できる MCP サーバー。

EDINET（金融庁）の有価証券報告書・四半期報告書・臨時報告書・大量保有報告書などをダウンロードし、SQLite FTS5 でローカルに全文検索インデックスを構築します。Claude Desktop、Cursor、VS Code Copilot などの MCP クライアントから直接利用できます。

## 何ができるか

**既存の EDINET MCP サーバーは財務数値の取得に特化しています。** このツールは有報の「テキスト」を検索対象にする点が異なります。

```
❌ 既存ツール: 「トヨタの売上高は？」 → 45兆953億円
✅ これ:       「半導体サプライチェーンリスクに言及している企業は？」
               → トヨタ: 「半導体サプライチェーンリスク、地政学的リスク...」(事業等のリスク)
               → ソニー: 「特定サプライヤーへの依存...」(事業等のリスク)
```

### MCP ツール（5つ）

| ツール | 説明 |
|---|---|
| `search_filings` | 有報テキストの全文検索（セクション・企業名フィルタ対応） |
| `get_filing_section` | 書類の特定セクション全文を取得 |
| `list_indexed_companies` | インデックス済み企業の一覧 |
| `get_index_stats` | インデックスの統計情報 |
| `compare_sections` | 2つの書類の同一セクションを比較（新出/消失キーワード検出） |

### 検索可能なセクション

- **事業等のリスク** — リスク要因の記述
- **事業の内容** — 事業概要
- **経営者による分析（MD&A）** — 経営成績・財政状態の分析
- **コーポレート・ガバナンスの状況等**
- **経理の状況**
- **全文** — 上記に分類されない部分を含む全テキスト

### 対象書類（18種別）

**主要（HIGH）**: 有価証券報告書、四半期報告書、半期報告書、臨時報告書 + 各訂正

**準主要（MEDIUM）**: 内部統制報告書、公開買付届出書/報告書、意見表明報告書、大量保有報告書 + 各訂正

## セットアップ

### 1. インストール

```bash
git clone https://github.com/yourname/japan-ir-search.git
cd japan-ir-search
uv sync
```

### 2. EDINET API キーの取得

[EDINET](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html) で無料登録し、Subscription-Key を取得してください。

```bash
export EDINET_API_KEY=your_key_here
```

### 3. インデックス構築

```bash
# 直近1年分（約2GB、2〜3時間）
uv run japan-ir-search build

# 直近3年分、全種別（約18GB、20〜30時間）
uv run japan-ir-search build --years 3 --doc-types all

# 主要種別のみ（有報・四半期・半期・臨時のみ）
uv run japan-ir-search build --years 3 --doc-types high

# バックグラウンド実行（推奨）
nohup uv run japan-ir-search -v build --years 3 > build.log 2>&1 &
tail -f build.log
```

中断しても再開時にインデックス済みの書類はスキップされます。

### 4. MCP サーバーとして使う

```bash
uv run japan-ir-search serve
```

Claude Desktop・Cursor・VS Code 等での MCP サーバーの設定方法は各クライアントのドキュメントを参照してください。サーバーコマンドは上記です。

- [Claude Desktop — MCP 設定ガイド](https://modelcontextprotocol.io/quickstart/user)
- [VS Code — MCP の使い方](https://code.visualstudio.com/docs/copilot/chat/mcp-servers)
- [Cursor — MCP 設定](https://docs.cursor.com/context/model-context-protocol)

設定例（共通パターン）:

```json
{
  "command": "uv",
  "args": ["--directory", "/path/to/japan-ir-search", "run", "japan-ir-search", "serve"],
  "env": { "EDINET_API_KEY": "your_key_here" }
}
```

## CLI

```bash
# 検索
uv run japan-ir-search search "半導体 サプライチェーン"
uv run japan-ir-search search "M&A 買収" --section risk_factors
uv run japan-ir-search search "ESG" --company トヨタ

# 統計確認
uv run japan-ir-search stats

# MCP サーバー起動
uv run japan-ir-search serve
```

## 使い方の例

MCP クライアント（Claude等）にこう聞けます：

- 「地政学リスクに言及している自動車メーカーを探して」
- 「ソニーの事業等のリスクを見せて」
- 「トヨタの前期と当期でリスク記述がどう変わったか比較して」
- 「臨時報告書でM&Aに言及している直近の書類は？」
- 「大量保有報告書でアクティビストの動きがある企業は？」

## アーキテクチャ

```
EDINET API → ZIP DL → HTML抽出 → セクション分割 → SQLite FTS5 (trigram)
                                                         ↓
                                              MCP Server ← Claude/Cursor/VS Code
```

- **SQLite FTS5 (trigram)**: 日本語テキストを文字 n-gram で全文検索
- **ローカル完結**: 外部サービス不要。DB ファイル 1つで動作
- **レート制限対策**: 0.8秒間隔 + 429/5xx 指数バックオフリトライ

## ストレージ目安

| 範囲 | 対象 | テキスト量 | FTS5 込み | CLI オプション |
|---|---|---|---|---|
| 直近1年 | 有報・四半期・半期・臨時 + 訂正 (HIGH 8種) | ~1.7GB | ~3GB | `--years 1 --doc-types high` |
| 直近1年 | 上記 + TOB・大量保有・内部統制等 (全18種) | ~3.2GB | ~6GB | `--years 1 --doc-types all` |
| 直近3年 | HIGH 8種 | ~5GB | ~9GB | `--years 3 --doc-types high` |
| 直近3年 | 全18種 | ~10GB | ~18GB | `--years 3 --doc-types all` |

書類種別の詳細は[対象書類](#対象書類18種別)を参照。

## データについて

[EDINET](https://disclosure.edinet-fsa.go.jp/)（Electronic Disclosure for Investors' NETwork）は金融庁が運営する法定開示書類の電子開示システムです。API 利用は無料（要登録）。開示データは[公共データライセンス 1.0](https://www.digital.go.jp/resources/open_data/) に基づき提供されています。

## ライセンス

MIT