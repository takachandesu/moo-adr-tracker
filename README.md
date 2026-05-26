# moo-adr-tracker

日本株ADR乖離率の自動取得・公開システム。

NY引け（米東部16:00）の円換算ADR価格と、東京前日終値（15:30）を比較し、乖離率ベスト20・ワースト20をWordPress上に表示します。

## 仕組み

```
┌─────────────────────┐    毎日 22:30 UTC（JST 07:30）    ┌──────────────┐
│  GitHub Actions     │ ───────────────────────────────▶ │ Yahoo Finance│
│  (cron)             │                                    └──────────────┘
└──────────┬──────────┘                                          │
           │                                                      │
           │  ① Pythonスクリプト実行                              │
           │     fetch_adr_data.py                                ▼
           │                                              全銘柄の終値取得
           │                                              USDJPY取得
           │                                              乖離率計算
           │                                                      │
           │  ② adr-data.json 生成 ◀──────────────────────────────┘
           │
           ▼
┌─────────────────────┐    FTP                            ┌──────────────┐
│  GitHub Actions     │ ────────────────────────────────▶ │ ロリポップ    │
│  FTP-Deploy-Action  │                                    │ /adr-data.json│
└─────────────────────┘                                    └──────┬───────┘
                                                                  │
                                                                  ▼
                                                          ┌──────────────┐
                                                          │  WordPress    │
                                                          │  カスタムHTML │
                                                          │ adr-widget.html│
                                                          │  でfetch表示  │
                                                          └──────────────┘
```

## ディレクトリ構成

```
moo-adr-tracker/
├── .github/workflows/update-adr.yml   # GitHub Actions定義
├── scripts/
│   ├── adr_list.json                  # ADR銘柄マスター
│   ├── fetch_adr_data.py              # データ取得スクリプト
│   └── requirements.txt               # Python依存パッケージ
├── public/
│   ├── adr-widget.html                # WordPress埋め込み用HTML
│   └── adr-data.json                  # 自動生成（gitignore推奨）
└── README.md
```

## セットアップ手順

### 1. GitHubリポジトリの準備

このプロジェクト一式を新しいGitHubリポジトリにpushします。

```bash
cd moo-adr-tracker
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/あなたのID/moo-adr-tracker.git
git push -u origin main
```

リポジトリは Public でも Private でもOK（GitHub ActionsはPrivateでも月2,000分まで無料）。

### 2. ロリポップFTP情報の準備

ロリポップ管理画面から以下を確認します。
- FTPサーバー名: `ftp.lolipop.jp` 形式の文字列
- FTPアカウント
- FTPパスワード
- アップロード先パス: WordPressの設置パス。例: `/home/users/0/lolipop.jp-XXXXX/web/`

`adr-data.json` をWordPressから `/adr-data.json` で参照したい場合、WordPressのルート（通常 `index.php` がある場所）に配置します。

### 3. GitHub Secretsに登録

GitHubリポジトリ → Settings → Secrets and variables → Actions → New repository secret

以下4つを登録します。

| Secret名 | 値の例 |
|----------|--------|
| `LOLIPOP_FTP_HOST` | `ftp.lolipop.jp` |
| `LOLIPOP_FTP_USER` | ロリポップのFTPアカウント |
| `LOLIPOP_FTP_PASSWORD` | ロリポップのFTPパスワード |
| `LOLIPOP_FTP_PATH` | `/web/` など（WordPressルート相対） |

### 4. 動作テスト

GitHub → Actions タブ → 「Update ADR Data」 → 「Run workflow」

手動で実行してログを確認します。エラーが出た場合は：
- `LOLIPOP_FTP_PATH` がロリポップ側のパスと合っているか
- ロリポップのFTPアカウントが有効か
- Yahoo Financeのレート制限に当たっていないか

### 5. WordPressに貼り付け

WordPress管理画面で：
1. 新規固定ページを作成（例：「日本株ADR乖離率」）
2. ブロックエディタで「カスタムHTML」ブロックを追加
3. `public/adr-widget.html` の内容を全てコピーして貼り付け
4. ウィジェット内の `DATA_URL` を環境に合わせて変更（同一ドメインなら `/adr-data.json` のままでOK）
5. 公開
6. メニューにこのページを追加

### 6. スケジュール確認

GitHub Actions は毎日 22:30 UTC（JST 07:30）に自動実行されます。
- 米国祝日：NYSE が休場なので、データは前日と同じ値を再書き出し
- 日本祝日：TSE が休場なので、東京終値は前営業日のもの

## ADR比率について

`scripts/adr_list.json` の `adr_ratio` は「1 ADRに対応する東京市場の株数」です。

例：
- Toyota Motor（TM）: `adr_ratio: 2` → 1 ADR = 東京株式2株分
- Nintendo（NTDOY）: `adr_ratio: 0.125` → 1 ADR = 東京株式0.125株分（1株 = 8 ADR）

**重要**: ADR比率は企業ごとに異なり、株式分割等で変更される場合があります。比率が間違っていると乖離率が体系的にずれます。

各銘柄の正確な比率は以下で確認：
- 預託銀行（BNY Mellon Depositary Receipts, JPMorgan, Citi）の銘柄ページ
- 企業のIRサイト
- ADR.com (https://www.adr.com)

実値と表示が大きく合わない銘柄があれば、まず比率を疑ってください。

## 銘柄の追加・削除

`scripts/adr_list.json` の `adrs` 配列を編集してgit pushすれば、次回実行から反映されます。

レベル区分の判定基準：
- レベル3：NASDAQに米国IPO（F-1登録）した企業。出来高薄く乖離が極端に出やすい
- レベル2：NYSE/NASDAQ上場（F-6登録）。資金調達なし
- レベル1：OTC店頭取引のスポンサー付きADR

## トラブルシューティング

**Yahoo Financeが取得できない**: yfinanceがYahooの仕様変更で動かなくなることがあります。`pip install --upgrade yfinance` で最新版に更新してください。

**FTPアップロードが失敗する**: ロリポップ側で「FTPアクセス制限」を有効にしているとIP制限に弾かれる可能性があります。GitHub ActionsのIPは固定ではないので、ロリポップ管理画面で制限を解除するか、IP範囲を許可してください。

**JSONが表示されない**: ブラウザの開発者ツール（F12）→ Networkタブで `/adr-data.json` のステータスコードを確認。404ならパスが違う、403ならパーミッション、CORSエラーなら別ドメインに置いている可能性があります。

**乖離率が異常値**: ADR比率の設定ミスがほぼ全てです。該当銘柄を `adr_list.json` で見直してください。

## ライセンス

私用・商用問わず自由に改変・利用可能。

## 投資判断について

本ツールは情報提供を目的としたものであり、投資助言ではありません。投資の最終判断はご自身の責任で行ってください。
