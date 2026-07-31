# AI Visibility KPI Board

生成AIの回答のなかで、自社ブランドとそのSNSがどれだけ使われているかを**毎日測って公開する**ためのリポジトリ。
Semrush等の有料GEOツールを使わず、公開APIと自社ログだけで構成している。

- 日次でプロンプトを実行 → 引用URLを分類 → 6因数スコアを算出
- 画面は**スコア（絶対値）が主役**。前日比 / 先週比 / 先月比は**括弧内の補助表示**で、7日移動中央値と±2σの有意判定つき
- **SNS・UGCをスコアの構成要素として組み込み済み**（アーンド引用率 = 20点）
- GitHub Actions が毎日走り、GitHub Pages に静的公開される
- **UI**：ダーク基調（Linearの黒キャンバス＋OP.GGのゲーム系密度を参照）／ライト切替、
  ティアバッジ（S/A/B/C/D）、6因数レーダー、セグメントゲージ、VS対戦パネル、
  **⌘K コマンドパレット**（全クエリ・自社ページを横断検索）、トースト通知、
  折れ線の十字カーソル＋ツールチップ、スクロール連動リビール、数値カウントアップ

```
docs/index.html  ← ダッシュボード（data/latest.json を読むだけ）
```

---

## 60秒で動かす

```bash
pip install -r requirements.txt
python src/run_daily.py --date 2026-07-30 --backfill 45   # デモデータを45日ぶん生成
cd docs && python -m http.server 8000                     # → http://localhost:8000
```

APIキーが1つも無くても動く（`GEO_BOARD_MODE=demo`）。実測値の分布に沿った合成データで、
UI・スコア・差分・アラートまで全部そのまま確認できる。

---

## 本番に切り替える

`GEO_BOARD_MODE=live` にして、必要な鍵を GitHub Secrets に入れるだけ。
**入れた鍵のぶんだけ実データに切り替わり、無いものはデモ値のまま**動き続ける設計。

| Secret | 用途 | 取得元 | 費用 |
|---|---|---|---|
| `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` | プロンプト実行と引用URL取得（本体） | [dataforseo.com](https://dataforseo.com/) でアカウント作成 → API Access | $0.0006/task + LLM実費。最低入金$50 |
| `GA4_PROPERTY_ID` | AI経由セッション | GA4 管理 → プロパティ設定 | 無料 |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | GA4 Data API 認証 | GCP → サービスアカウント → JSONキー → GA4に閲覧者権限付与 | 無料 |
| `YOUTUBE_API_KEY` / `YOUTUBE_CHANNEL_ID` | 自社チャンネル指標 | GCP → APIとサービス → YouTube Data API v3 | 無料（10,000ユニット/日） |
| `ACCESS_LOG_PATH` | AIボット到達（変数で指定） | 自社サーバー/CDNのアクセスログ | 無料 |
| `SLACK_WEBHOOK_URL` | 日次通知 | Slack App → Incoming Webhooks | 無料 |

> **課金が発生するのは DataForSEO だけ**。500プロンプト × 4サーフェス × 日次で月 $50〜300 の想定。
> 最初は `config/settings.yaml` の `tier_schedule.core.max_prompts` を小さくして様子を見ること。

### Google Ads のキーワードボリュームを使う場合

Keyword Planner のデータは Google Ads API から無料で取れるが、3つ注意がある。

1. 広告出稿の実績がないアカウントでは、月間ボリュームが「1万〜10万」のレンジに丸められる
2. `KeywordPlanIdeaService` は Basic Access 以上が必要（審査 約5営業日）。seed は1リクエスト20キーワードまで、1 QPS
3. **規約上、第三者に見せるダッシュボードへの表示はグレー**。Permissible Use の "keyword research" 区分は広告キャンペーン管理を前提としており、Standard Access では RMF（広告作成・管理機能のフル実装）を要求される。**Internal Use Only なら RMF 適用外**と公式に明記されている

→ **公開ダッシュボードで使うなら、DataForSEO の Google Ads 再販エンドポイント（$0.05〜0.09 / 1,000キーワード）経由が安全。**

---

## クエリ別の被引用URL

各クエリで**自社のどのページが引かれているか**をURL単位で保持している。

- 「クエリ」タブ … 行をクリックすると、その回答に添えられた**全引用URL**が展開される。
  自社オウンド / グループ・販売店 / SNS・UGC / 一般メディア / 競合 / ノイズ に色分けされ、
  どのAIサーフェスで引かれたかがタグで付く
- 「自社の被引用ページ」タブ … **逆引き**。`/carlineup/` がどのクエリで何回引かれたかが分かる。
  下段には「自社ページが1つも引かれていないクエリ」と、**代わりにAIが根拠にしている他人のURL**が並ぶ

データ的には `data/snapshots/*.json` の `cells[].citations[]` が原本で、
`docs/data/latest.json` の `queries[].citations` / `queries[].own_pages` / `owned_pages` に整形される。
本番では DataForSEO の `annotations`（回答に添えられた引用）がそのまま入る。

---

## 公開する

```bash
# 1. GitHubに public リポジトリとして push
# 2. Settings → Pages → Source: GitHub Actions
# 3. Settings → Secrets and variables → Actions に上表の鍵を登録
# 4. Actions タブ → daily-geo-board → Run workflow で初回実行
```

- public リポジトリなら **Actions の実行時間は無制限で無料**
- cron は `17 22 * * *`（07:17 JST）。GitHub公式が「毎時0分付近は遅延・ドロップされ得る」と明記しているため、意図的に0分を外している
- **public リポジトリは60日間コミットが無いと schedule が自動無効化される**。日次コミットがあるので通常は問題ないが、長期停止後は再有効化が必要

### 限定公開にしたい場合

GitHub Pages の非公開化は Enterprise Cloud 限定。
無料でやるなら **Cloudflare Access（50ユーザーまで無料）** を前段に置き、メール認証をかける。

---

## スコアの定義

```
GEO_SCORE = 出現率×25 + 順位品質×15 + オウンド引用率×20
          + アーンド引用率×20 + センチメント×10 + 相対シェア×10   （合計100）
```

| 因数 | 定義 | 落ちたときの打ち手 |
|---|---|---|
| 出現率 | 言及された回答数 ÷ 全回答数 | 候補に入っていない。トピック網羅の拡張 |
| 順位品質 | Σ(1÷登場順位) ÷ 言及回答数 | 入っても後ろ。根拠の厚み不足 |
| オウンド引用率 | 自社ドメインが引用された割合 | 構造化・FAQ整備・robots.txt |
| **アーンド引用率** | **Σ(プラットフォーム重み × 自社シェア)** | **YouTube・note・アンバサダー施策** |
| センチメント | 好意的と判定された割合 | ネガ要因タグ別に対処 |
| 相対シェア | 自社言及数 ÷ 競合合計 | 総合的な露出量 |

**アーンド引用率がSNSの評価そのもの。** `config/platforms.yaml` の `weight` を変えれば、
どのプラットフォームを重視するかを事業判断として反映できる。

### 差分の扱い

LLMは非決定的で、同一プロンプトでもモデル間の不一致率は5割を超える。
そのため生の前日比は使わず、以下の3段構えにしている。

1. 1プロンプトを複数回実行し**中央値**をその日の値にする（`sampling.runs_per_prompt`）
2. 表示する差分は**7日移動中央値どうしの差**
3. 過去30日の日次標準偏差から**±2σ以内は「有意差なし」**とグレー表示（色を付けない）

スコア差分は必ず**因数ごとの寄与（重み×因数差）に分解**され、
アーンド引用率はさらに**プラットフォーム別の寄与**まで割れる。

---

## ディレクトリ

```
config/
  settings.yaml     実行するサーフェス、サンプリング回数、スコア重み、有意判定の閾値
  brands.yaml       自社・競合の表記ゆれとドメイン、判断軸14、ネガ要因タグ7
  platforms.yaml    SNS/UGCの重みとドメイン ★アーンド引用スコアの心臓部
prompts/
  core.yaml         日次実行するプロンプト（知恵袋の実文 + PAA + GSC由来）
src/
  collect/llm.py       AIサーフェスへの実行（DataForSEO / SERP / demo）
  collect/signals.py   GA4・AIボットログ・YouTube
  analyze.py           ブランド判定・引用分類・6因数スコア
  diff.py              DoD/WoW/MoM・移動中央値・有意判定・要因分解
  comment.py           自動コメントとアラート
  build_site.py        docs/data/latest.json の生成
  run_daily.py         日次パイプライン本体
  notify.py            Slack通知
data/snapshots/       日次スナップショット（これが資産。消さないこと）
docs/                 GitHub Pages の公開ルート
```

## 運用のコツ

- **`data/snapshots/` は絶対に消さない。** 前年比を出せるようになるまで1年かかる資産
- プロンプトは足すのは自由だが、**コア枠のIDは変えない**。時系列が切れる
- `config/brands.yaml` の `drivers` で `priority: high` にした軸は、必ずコアプロンプトに含める
- 月1回、`config/platforms.yaml` の `weight` を実測の被引用構成比で更新する
