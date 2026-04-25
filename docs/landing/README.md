# LP (Landing Page) — Cloudflare Pages デプロイ手順

非エンジニア層向けランディングページ。1ページ完結の静的 HTML。

## ファイル構成

```
docs/landing/
├── index.html          ← LP 本体（このディレクトリのみで完結）
├── README.md           ← この手順書
└── assets/
    ├── demo.mp4        ← デモ動画（要配置）
    ├── demo-poster.jpg ← 動画ポスター（要配置）
    └── og.png          ← OGP 画像 1200×630（要配置）
```

## プレースホルダ → 実画像への差し替え

### 1. デモ動画 (`assets/demo.mp4`)

CleanShot 等で撮った動画を ffmpeg で最適化してから配置:

```bash
ffmpeg -i ~/Downloads/demo-raw.mp4 \
  -vf "scale=1280:-2,fps=24" \
  -c:v libx264 -preset slow -crf 28 \
  -an -movflags +faststart \
  docs/landing/assets/demo.mp4
```

目標サイズ: **5MB 以下**、長さ **20〜30秒**。

### 2. ポスター画像 (`assets/demo-poster.jpg`)

動画の最終フレーム（5社リストが表示されている場面）を 1 枚抽出:

```bash
ffmpeg -ss 18 -i docs/landing/assets/demo.mp4 \
  -frames:v 1 -q:v 2 \
  docs/landing/assets/demo-poster.jpg
```

### 3. video タグの有効化

`index.html` の以下のコメントを外す:

```html
<!--
<video autoplay loop muted playsinline preload="metadata" poster="assets/demo-poster.jpg">
  <source src="assets/demo.mp4" type="video/mp4">
</video>
-->
```

そして `<div class="placeholder">...</div>` を削除。

### 4. OGP 画像 (`assets/og.png`) — 任意

1200×630 の画像。Figma / Canva で「japan-ir-search · AIに有報を読ませる」のようなタイトル + デモのスクショ合成で作成。後回しでも OK。

## ユーザー名の置換

`index.html` 内の `tamcho113` と `tamcho` (note ID) は既に設定済み。変更が必要な場合:

```bash
cd docs/landing
sed -i '' 's/tamcho113/your-actual-name/g' index.html
sed -i '' 's|note.com/tamcho|note.com/your-note-name|g' index.html
```

## ローカルプレビュー

```bash
cd docs/landing
python3 -m http.server 8000
# → http://localhost:8000 を開く
```

## Cloudflare Pages へのデプロイ

### 方法 A: Direct Upload（簡単・推奨）

1. <https://dash.cloudflare.com/> にログイン（無ければ作成）
2. 左メニュー **Workers & Pages** → **Create application** → **Pages** タブ
3. **Upload assets** を選択
4. プロジェクト名: `japan-ir-search`
5. `docs/landing/` フォルダごとドラッグ&ドロップ
6. Deploy

URL: `https://japan-ir-search.pages.dev/`

### 方法 B: Git 連携（更新が楽・将来推奨）

1. GitHub リポジトリと連携
2. Build settings:
   - Framework preset: **None**
   - Build command: 空
   - Build output directory: `docs/landing`
3. Deploy

→ 以後、`main` への push で自動デプロイ。

## 独自ドメイン（任意）

Cloudflare で `japan-ir-search.dev` 等を取得 (年 ~$10) → Pages の Custom Domain で紐付け。

## メンテナンス

- 収録件数の更新 → `index.html` の「3.7万件超」「6,300 社」を書き換え
- 新クライアント対応 → タブを追加（`.tab-btn` と `.tab-content` を増やす）
- FAQ 追加 → `<details>` ブロックを追加するだけ
