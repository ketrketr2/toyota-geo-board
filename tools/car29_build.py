#!/usr/bin/env python3
"""①車種別AI分析の29車種版（tier=car29）の設定と質問を作る。

  python tools/car29_build.py   → config/cars29.yaml と prompts/car29.yaml を書き出す

方針
- 元の8車種の設定（config/cars.yaml）と質問（prompts/registry.yaml の tier=car）は変えない。
  元の8車種の質問は、そのまま写す（ランドクルーザーの id だけ lc250 → landcruiser）。対象の車（cars）も元のまま。
- 29車種は、トヨタコニックの広告ダッシュボード（AD SIGNAL）の車種のページ（K1）と同じ。id もそろえる。
- 新しい21車種の質問は、このファイルの中で作る。対象の車（cars）は新しい21車種だけ。
  元の8車種の出現率が、前回（2026-08-28）と同じ質問の集合で比べられるようにするため。
- 質問文に出てくる車（named_cars）は、29車種の別名で照合し直す。その車の出現率からは除く（自明に出るため）。
- 結果は data/car/wide/<日付>.json（run_car.py --tiers car29）。元の8車種の結果（data/car/snapshots/）とは混ぜない。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TODAY = "2026-11-13"
OLD2NEW = {"lc250": "landcruiser"}

# ---------------------------------------------------------------- 型
SEGS_NEW = {
    "suv_mid": "ミドルSUV",
    "sedan_c": "セダン",
    "wagon": "ステーションワゴン",
    "hatch_c": "ハッチバック",
    "premium": "上級セダン・クロスオーバー",
    "hybrid": "ハイブリッド",
    "ev": "電動車（EV・燃料電池車）",
    "sports": "スポーツカー",
    "pickup": "ピックアップ",
    "chauffeur": "ショーファーカー",
    "van": "商用バン",
}

# ---------------------------------------------------------------- 新しい21車種
NEW_FOCUS = [
    dict(id="yaris", name="ヤリス", seg="compact_hb", slug="/yaris", aliases=["ヤリス", "YARIS"],
         rivals=["fit", "note", "swift", "mazda2"], rivals_own=["aqua", "yariscross"]),
    dict(id="yariscross", name="ヤリス クロス", seg="suv_compact", slug="/yariscross", aliases=["ヤリスクロス", "ヤリス クロス", "YARIS CROSS"],
         rivals=["vezel", "wrv", "kicks", "fronx", "cx30"], rivals_own=["raize", "corollacross", "yaris"]),
    dict(id="corolla", name="カローラ", seg="sedan_c", slug="/corolla",
         aliases=["カローラセダン", "カローラ セダン", "カローラアクシオ", "カローラ アクシオ", "カローラフィールダー", "カローラ フィールダー", "カローラ", "COROLLA"],
         guards=["トヨタカローラ店", "カローラ店", "カローラ販売店"],
         rivals=["mazda3", "impreza", "civic"], rivals_own=["prius", "corollatouring", "corollasport"]),
    dict(id="corollatouring", name="カローラ ツーリング", seg="wagon", slug="/corollatouring", aliases=["カローラツーリング", "カローラ ツーリング", "COROLLA TOURING"],
         rivals=["levorg", "legacy"], rivals_own=["corolla", "crownestate"]),
    dict(id="corollasport", name="カローラ スポーツ", seg="hatch_c", slug="/corollasport", aliases=["カローラスポーツ", "カローラ スポーツ", "COROLLA SPORT"],
         rivals=["civic", "mazda3", "impreza", "vw_golf"], rivals_own=["yaris", "corolla", "gr_corolla"]),
    dict(id="corollacross", name="カローラ クロス", seg="suv_mid", slug="/corollacross", aliases=["カローラクロス", "カローラ クロス", "COROLLA CROSS"],
         rivals=["vezel", "zrv", "cx30", "kicks"], rivals_own=["yariscross", "rav4", "harrier"]),
    dict(id="crown", name="クラウン", seg="premium", slug="/crown",
         aliases=["クラウンセダン", "クラウン セダン", "クラウン（セダン）", "クラウン", "CROWN"], guards=["クラウンメロン", "中日クラウンズ"],
         rivals=["accord", "e_class", "bmw5", "lexus_es"], rivals_own=["crowncrossover", "century"]),
    dict(id="crownsport", name="クラウン（スポーツ）", seg="premium", slug="/crownsport",
         aliases=["クラウンスポーツ", "クラウン スポーツ", "クラウン（スポーツ）", "CROWN SPORT"],
         rivals=["cx60", "lexus_nx", "glc"], rivals_own=["harrier", "crowncrossover"]),
    dict(id="crowncrossover", name="クラウン（クロスオーバー）", seg="premium", slug="/crowncrossover",
         aliases=["クラウンクロスオーバー", "クラウン クロスオーバー", "クラウン（クロスオーバー）", "CROWN CROSSOVER"],
         rivals=["accord", "cx60", "legacy"], rivals_own=["crown", "crownsport", "prius"]),
    dict(id="crownestate", name="クラウン（エステート）", seg="premium", slug="/crownestate",
         aliases=["クラウンエステート", "クラウン エステート", "クラウン（エステート）", "CROWN ESTATE"],
         rivals=["cx80", "legacy", "volvo"], rivals_own=["harrier", "crownsport"]),
    dict(id="prius", name="プリウス", seg="hybrid", slug="/prius", aliases=["プリウス", "PRIUS"],
         rivals=["civic", "mazda3", "impreza"], rivals_own=["aqua", "corolla"]),
    dict(id="harrier", name="ハリアー", seg="suv_mid", slug="/harrier", aliases=["ハリアー", "HARRIER"],
         rivals=["cx5", "cx60", "xtrail", "zrv", "forester"], rivals_own=["rav4", "crownsport", "corollacross"]),
    dict(id="rav4", name="RAV4", seg="suv_mid", slug="/rav4", aliases=["RAV4", "RAV 4", "ＲＡＶ４", "ラブフォー", "rav4"],
         rivals=["xtrail", "forester", "cx5", "outlander"], rivals_own=["harrier", "corollacross", "landcruiser"]),
    dict(id="bz4x", name="bZ4X", seg="ev", slug="/bz4x", aliases=["bZ4X", "BZ4X", "bz4x"],
         rivals=["leaf", "ariya", "model_y", "byd"], rivals_own=["prius", "mirai"]),
    dict(id="mirai", name="MIRAI", seg="ev", slug="/mirai", aliases=["MIRAI", "Mirai", "ミライ"], guards=["ミライース", "ミライトワ", "ミライト", "ミライズ"],
         rivals=["nexo", "crv"], rivals_own=["bz4x", "crown"]),
    dict(id="vellfire", name="ヴェルファイア", seg="minivan_l", slug="/vellfire", aliases=["ヴェルファイア", "ベルファイア", "VELLFIRE"],
         rivals=["elgrand", "odyssey", "delica_d5", "lexus_lm"], rivals_own=["alphard", "noah", "voxy"]),
    dict(id="gr86", name="GR86", seg="sports", slug="/gr86", aliases=["GR86", "GR 86", "ハチロク", "トヨタ86", "トヨタ 86", "TOYOTA 86"],
         rivals=["brz", "roadster", "fairladyz"], rivals_own=["gr_yaris", "gr_supra", "corollasport"]),
    dict(id="hilux", name="ハイラックス", seg="pickup", slug="/hilux", aliases=["ハイラックス", "HILUX"],
         rivals=["triton"], rivals_own=["landcruiser"]),
    dict(id="century", name="センチュリー", seg="chauffeur", slug="/century", aliases=["センチュリー", "CENTURY"],
         guards=["センチュリー21", "センチュリー２１", "センチュリーハイアット", "センチュリオン", "センチュリーシネマ"],
         rivals=["lexus_ls", "s_class", "rolls"], rivals_own=["crown", "alphard"]),
    dict(id="probox", name="プロボックス", seg="van", slug="/probox", aliases=["プロボックス", "PROBOX", "サクシード"],
         rivals=["nissan_ad", "nv200"], rivals_own=["hiacevan"]),
    dict(id="hiacevan", name="ハイエース", seg="van", slug="/hiace", aliases=["ハイエース", "HIACE"],
         rivals=["caravan", "nv200"], rivals_own=["probox"]),
]

# ---------------------------------------------------------------- 競合（検出用の別名）。元の cars.yaml の rivals に足す
NEW_RIVALS = [
    ("swift", "スイフト", "suzuki", ["スイフト", "スウィフト", "SWIFT"], []),
    ("mazda2", "MAZDA2", "mazda", ["MAZDA2", "Mazda2", "マツダ2", "マツダ２", "デミオ"], []),
    ("civic", "シビック", "honda", ["シビック", "CIVIC"], ["シビックプライド", "シビックテック"]),
    ("mazda3", "MAZDA3", "mazda", ["MAZDA3", "Mazda3", "マツダ3", "マツダ３", "アクセラ"], []),
    ("impreza", "インプレッサ", "subaru", ["インプレッサ", "IMPREZA"], []),
    ("levorg", "レヴォーグ", "subaru", ["レヴォーグ", "LEVORG"], []),
    ("legacy", "レガシィ アウトバック", "subaru", ["レガシィアウトバック", "レガシィ アウトバック", "アウトバック", "レガシィ", "OUTBACK"], []),
    ("forester", "フォレスター", "subaru", ["フォレスター", "FORESTER"], []),
    ("brz", "BRZ", "subaru", ["BRZ"], []),
    ("cx5", "CX-5", "mazda", ["CX-5", "CX5", "ＣＸ－５"], []),
    ("cx30", "CX-30", "mazda", ["CX-30", "CX30", "ＣＸ－３０"], []),
    ("cx60", "CX-60", "mazda", ["CX-60", "CX60", "ＣＸ－６０"], []),
    ("cx80", "CX-80", "mazda", ["CX-80", "CX80", "ＣＸ－８０"], []),
    ("roadster", "ロードスター", "mazda", ["ロードスター", "ROADSTER"], []),
    ("accord", "アコード", "honda", ["アコード", "ACCORD"], []),
    ("zrv", "ZR-V", "honda", ["ZR-V", "ZRV"], []),
    ("vezel", "ヴェゼル", "honda", ["ヴェゼル", "VEZEL"], []),
    ("odyssey", "オデッセイ", "honda", ["オデッセイ", "ODYSSEY"], []),
    ("crv", "CR-V", "honda", ["CR-V", "CRV"], []),
    ("kicks", "キックス", "nissan", ["キックス", "KICKS"], ["キックスケーター", "キックスタート", "キックスクーター"]),
    ("leaf", "リーフ", "nissan", ["リーフ", "LEAF"], ["リーフレット", "リーフパイ", "リーフレタス", "ブルーリーフ"]),
    ("ariya", "アリア", "nissan", ["日産アリア", "日産 アリア", "アリア", "ARIYA"], ["マリア", "アリアナ", "ダリア", "アリアス"]),
    ("fairladyz", "フェアレディZ", "nissan", ["フェアレディZ", "フェアレディ Z", "フェアレディ"], []),
    ("caravan", "キャラバン", "nissan", ["キャラバン", "CARAVAN", "NV350"], ["キャラバンサライ", "キャラバン隊"]),
    ("nv200", "NV200バネット", "nissan", ["NV200バネット", "NV200", "バネット"], []),
    ("nissan_ad", "ADバン", "nissan", ["ADバン", "AD バン", "ADエキスパート", "日産AD", "日産 AD"], []),
    ("fronx", "フロンクス", "suzuki", ["フロンクス", "FRONX"], []),
    ("delica_d5", "デリカD:5", "mitsubishi", ["デリカD:5", "デリカ D:5", "デリカD5", "デリカ D5"], []),
    ("triton", "トライトン", "mitsubishi", ["トライトン", "TRITON"], []),
    ("nexo", "ネッソ", "hyundai", ["ネッソ", "NEXO"], []),
    ("model_y", "テスラ モデルY", "tesla", ["モデルY", "モデル Y", "Model Y", "MODEL Y"], []),
    ("model_3", "テスラ モデル3", "tesla", ["モデル3", "モデル 3", "Model 3", "MODEL 3"], []),
    ("byd", "BYD（各車）", "byd", ["BYD", "ビーワイディー"], []),
    ("vw_golf", "フォルクスワーゲン ゴルフ", "vw", ["フォルクスワーゲン ゴルフ", "フォルクスワーゲンゴルフ", "VWゴルフ", "VW ゴルフ", "Golf"], []),
    ("e_class", "メルセデス Eクラス", "mercedes", ["Eクラス", "E クラス"], []),
    ("s_class", "メルセデス Sクラス・マイバッハ", "mercedes", ["マイバッハ", "Sクラス", "S クラス"], []),
    ("glc", "メルセデス GLC", "mercedes", ["GLC"], []),
    ("bmw5", "BMW 5シリーズ", "bmw", ["5シリーズ", "5 シリーズ"], []),
    ("volvo", "ボルボ（XC60・V90 など）", "volvo", ["XC60", "XC40", "XC90", "V90", "V60"], []),
    ("rolls", "ロールス・ロイス", "rolls", ["ロールス・ロイス", "ロールスロイス", "ロールス ロイス", "ROLLS-ROYCE"], []),
    ("lexus_ls", "レクサス LS", "lexus", ["レクサスLS", "レクサス LS", "LS500h", "LS500"], []),
    ("lexus_es", "レクサス ES", "lexus", ["レクサスES", "レクサス ES", "ES300h"], []),
    ("lexus_nx", "レクサス NX", "lexus", ["レクサスNX", "レクサス NX", "NX350h", "NX450h+"], []),
    ("lexus_rx", "レクサス RX", "lexus", ["レクサスRX", "レクサス RX", "RX350h", "RX500h", "RX450h+"], []),
    ("lexus_lm", "レクサス LM", "lexus", ["レクサスLM", "レクサス LM", "LM500h"], []),
    ("lexus_lbx", "レクサス LBX", "lexus", ["レクサスLBX", "レクサス LBX", "LBX"], []),
    ("gr_yaris", "GRヤリス", "toyota", ["GRヤリス", "GR ヤリス", "GR YARIS"], []),
    ("gr_corolla", "GRカローラ", "toyota", ["GRカローラ", "GR カローラ", "GR COROLLA"], []),
    ("gr_supra", "GRスープラ", "toyota", ["GRスープラ", "GR スープラ", "スープラ", "SUPRA"], []),
    ("chr", "C-HR", "toyota", ["C-HR", "CHR"], []),
    ("camry", "カムリ", "toyota", ["カムリ", "CAMRY"], []),
]

# ---------------------------------------------------------------- 新しい21車種の質問
# (文, 種類, きっかけ, 検索語, 対象の車, 型)。対象の車は新しい21車種だけ。車名を含む質問は、その車の出現率からは除かれる。
Q = []
def q(text, cat, drv, kw, cars, seg):
    Q.append(dict(text=text, category=cat, driver=drv, keyword=kw, cars=cars, seg=seg))

# ヤリス
for t, c, d, k, cs in [
    ("通勤で毎日往復40km走ります。燃費が良くて手頃な価格の普通車を教えてください。", "cost", "commute", "燃費 いい 車 通勤", ["yaris"]),
    ("運転に自信がない人でも扱いやすい、取り回しのいいコンパクトカーはどれですか？", "model", "beginner", "運転しやすい コンパクトカー", ["yaris"]),
    ("150万円台から買える新車の普通車はありますか？安全装備も十分なものを教えてください。", "cost", "cost", "安い 新車 普通車", ["yaris"]),
    ("都内の狭い道と駐車場で使います。小回りの利く普通車を教えてください。", "model", "city", "小回り 車 普通車", ["yaris"]),
    ("マニュアル車（MT）で乗れる新車のコンパクトカーはありますか？", "model", "sport", "MT コンパクトカー 新車", ["yaris"]),
    ("スイフトとフィットで迷っています。同じクラスで比べておくべき車はありますか？", "model", "compare", "スイフト フィット 比較", ["yaris"]),
    ("高齢の親の買い物用に、軽自動車より安全な小さめの普通車を教えてください。", "safety", "senior", "高齢者 車 おすすめ 普通車", ["yaris"]),
    ("年間2万km走る営業職です。燃費で元が取れるハイブリッドのコンパクトカーはどれですか？", "eco", "business", "ハイブリッド コンパクトカー 燃費", ["yaris"]),
    ("4WDのコンパクトカーで、雪道でも安心して使えるのはどれですか？", "model", "snow", "4WD コンパクトカー 雪道", ["yaris"]),
    ("中古で総額100万円以下の、燃費の良いコンパクトカーを教えてください。", "cost", "used", "中古 コンパクトカー 燃費", ["yaris"]),
    ("自動ブレーキなどの安全装備を、コンパクトカーで横並びに比べてください。", "safety", "safety", "コンパクトカー 安全装備 比較", ["yaris"]),
    ("若い人に人気の、デザインの良い手頃なコンパクトカーは？", "purchase", "design", "コンパクトカー 人気 若者", ["yaris"]),
    ("MAZDA2が気になっています。同じクラスで比較すべき車はどれですか？", "model", "compare", "MAZDA2 ライバル", ["yaris"]),
    ("燃費が一番いい普通車は何ですか？カタログ値と実燃費の両方で教えてください。", "eco", "eco", "燃費 一番いい 車", ["yaris", "prius"]),
    ("ヤリスの欠点は何ですか？後席や荷室の狭さは実際どのくらいですか？", "brand", "named", "ヤリス 欠点", ["yaris"]),
    ("ヤリスのハイブリッドとガソリン車、どちらを選ぶべきですか？", "eco", "named", "ヤリス ハイブリッド ガソリン", ["yaris"]),
    ("ヤリスの値引き相場を教えてください。", "cost", "named", "ヤリス 値引き", ["yaris"]),
    ("ヤリスの実燃費はどのくらいですか？", "eco", "named", "ヤリス 実燃費", ["yaris"]),
    ("ヤリスとフィットを比較してください。どんな人にどちらが向きますか？", "model", "named", "ヤリス フィット 比較", ["yaris"]),
    ("ヤリスの中古は何年式が狙い目ですか？", "cost", "named", "ヤリス 中古", ["yaris"]),
    ("ヤリスの納期は今どのくらいですか？", "service", "named", "ヤリス 納期", ["yaris"]),
]:
    q(t, c, d, k, cs, "compact_hb")

# ヤリス クロス
for t, c, d, k, cs in [
    ("ヴェゼルを検討中です。同じくらいの価格で比べるべきSUVはありますか？", "model", "compare", "ヴェゼル ライバル", ["yariscross", "corollacross"]),
    ("燃費の良いハイブリッドSUVを、実燃費の良い順に教えてください。", "eco", "eco", "SUV 燃費 ランキング", ["yariscross", "corollacross"]),
    ("300万円以内で買える、燃費の良いSUVはどれですか？", "cost", "cost", "300万 SUV 燃費", ["yariscross", "corollacross"]),
    ("小さめのSUVで、高速道路も快適に走れる車を教えてください。", "model", "comfort", "コンパクトSUV 高速", ["yariscross"]),
    ("若い夫婦が最初に買うSUVとして、おすすめはどれですか？", "purchase", "family", "SUV 初めて おすすめ", ["yariscross", "corollacross"]),
    ("車高が高くて運転しやすいハイブリッド車を教えてください。", "model", "beginner", "車高が高い ハイブリッド", ["yariscross"]),
    ("雪国に住んでいます。4WDのハイブリッドSUVで安心して使えるのはどれですか？", "model", "snow", "4WD ハイブリッド SUV", ["yariscross", "corollacross", "rav4"]),
    ("フロンクスが気になっています。比較すべき国産の小型SUVはどれですか？", "model", "compare", "フロンクス 比較", ["yariscross"]),
    ("キックスと同じ価格帯のSUVを比べたいです。候補を教えてください。", "model", "compare", "キックス ライバル", ["yariscross"]),
    ("リセールバリューの良いコンパクトSUVはどれですか？", "cost", "resale", "コンパクトSUV リセール", ["yariscross"]),
    ("女性に人気のコンパクトSUVを教えてください。運転のしやすさも重視です。", "purchase", "design", "女性 人気 SUV", ["yariscross"]),
    ("中古で200万円前後のSUVを探しています。年式が新しめでおすすめは？", "cost", "used", "中古 SUV 200万", ["yariscross", "corollacross"]),
    ("荷室が使いやすいコンパクトSUVを比較してください。ゴルフバッグも積みたいです。", "model", "utility", "コンパクトSUV 荷室", ["yariscross"]),
    ("CX-30と比べるべきコンパクトSUVはどれですか？", "model", "compare", "CX-30 ライバル", ["yariscross", "corollacross"]),
    ("ヤリスクロスの欠点は何ですか？乗り心地や後席の広さはどうですか？", "brand", "named", "ヤリスクロス 欠点", ["yariscross"]),
    ("ヤリスクロスとヴェゼルを比較してください。", "model", "named", "ヤリスクロス ヴェゼル", ["yariscross"]),
    ("ヤリスクロスの納期は今どのくらいですか？", "service", "named", "ヤリスクロス 納期", ["yariscross"]),
    ("ヤリスクロスのグレードはどれがおすすめですか？", "model", "named", "ヤリスクロス グレード", ["yariscross"]),
    ("ヤリスクロスの値引き相場を教えてください。", "cost", "named", "ヤリスクロス 値引き", ["yariscross"]),
    ("ヤリスクロスの中古の相場と、狙い目の年式を教えてください。", "cost", "named", "ヤリスクロス 中古", ["yariscross"]),
]:
    q(t, c, d, k, cs, "suv_compact")

# ミドルSUV（ハリアー・RAV4・カローラ クロス）
for t, c, d, k, cs in [
    ("ミドルサイズの人気SUVを、価格・燃費・広さで比較してください。", "model", "compare", "ミドルサイズ SUV 比較", ["harrier", "rav4", "corollacross"]),
    ("400万円前後で買えるSUVを、燃費と装備で比べてください。", "cost", "cost", "400万 SUV", ["harrier", "rav4"]),
    ("内装が上質な国産SUVを教えてください。", "model", "luxury", "内装 上質 SUV", ["harrier", "crownsport"]),
    ("家族4人でキャンプによく行きます。荷物が積めるSUVのおすすめは？", "purchase", "outdoor", "キャンプ SUV 家族", ["rav4", "corollacross"]),
    ("エクストレイルを検討しています。比べるべき国産SUVはどれですか？", "model", "compare", "エクストレイル ライバル", ["rav4", "harrier", "corollacross"]),
    ("CX-5と同じクラスで、いま買えるSUVを比較してください。", "model", "compare", "CX-5 ライバル", ["harrier", "rav4", "corollacross"]),
    ("フォレスターと比べるべきSUVはどれですか？", "model", "compare", "フォレスター ライバル", ["rav4", "harrier"]),
    ("プラグインハイブリッドのSUVで、選べる車種を教えてください。", "eco", "ev", "PHEV SUV", ["rav4", "harrier"]),
    ("街乗りが中心で、たまに悪路や雪道も走るSUVを選ぶなら？", "model", "outdoor", "SUV 悪路 街乗り", ["rav4"]),
    ("見た目がかっこよくて、通勤にも使える都会的なSUVを教えてください。", "purchase", "design", "かっこいい SUV", ["harrier"]),
    ("リセールバリューの高いSUVをランキングで教えてください。", "cost", "resale", "SUV リセール ランキング", ["harrier", "rav4"]),
    ("30代のファミリーに人気のSUVはどれですか？", "purchase", "family", "ファミリー SUV 人気", ["harrier", "rav4", "corollacross"]),
    ("子供2人の家族で荷物が多いです。荷室の広いSUVを教えてください。", "model", "family", "荷室 広い SUV", ["rav4", "corollacross"]),
    ("予算350万円で、家族用のSUVを新車で買うなら何がいいですか？", "purchase", "family", "350万 SUV 家族", ["corollacross", "rav4", "harrier"]),
    ("燃費の良い4WDのハイブリッドSUVを比較してください。", "eco", "eco", "4WD ハイブリッド SUV 燃費", ["rav4", "harrier"]),
    ("ZR-Vと迷っています。同クラスで比較すべきSUVを教えてください。", "model", "compare", "ZR-V ライバル", ["harrier", "corollacross"]),
    ("40代の夫婦2人で乗る、上品なSUVを教えてください。", "purchase", "luxury", "SUV 上品 40代", ["harrier", "crownsport"]),
    ("アウトランダーのPHEVと比べるべきSUVはどれですか？", "model", "compare", "アウトランダー PHEV 比較", ["rav4", "harrier"]),
    ("走りの良いハイブリッドSUVを国産で選ぶなら？", "model", "sport", "走り ハイブリッド SUV", ["crownsport", "rav4"]),
    ("ハリアーの欠点は何ですか？後席や荷室の使い勝手はどうですか？", "brand", "named", "ハリアー 欠点", ["harrier"]),
    ("ハリアーとRAV4の違いは何ですか？どちらを選ぶべきですか？", "model", "named", "ハリアー RAV4 違い", ["harrier", "rav4"]),
    ("ハリアーのリセールバリューはどのくらいですか？残価設定ローンで買うのは得ですか？", "cost", "named", "ハリアー リセール", ["harrier"]),
    ("ハリアーの納期は今どのくらいですか？", "service", "named", "ハリアー 納期", ["harrier"]),
    ("ハリアーのPHEVの評価はどうですか？ハイブリッドとどちらが得ですか？", "eco", "named", "ハリアー PHEV", ["harrier"]),
    ("ハリアーの中古相場と、狙い目の年式を教えてください。", "cost", "named", "ハリアー 中古", ["harrier"]),
    ("ハリアーの値引き相場を教えてください。", "cost", "named", "ハリアー 値引き", ["harrier"]),
    ("新型RAV4の評価はどうですか？旧型から何が変わりましたか？", "model", "named", "新型RAV4 評価", ["rav4"]),
    ("RAV4のPHEVとハイブリッドはどちらが得ですか？", "eco", "named", "RAV4 PHEV ハイブリッド", ["rav4"]),
    ("RAV4の欠点は何ですか？", "brand", "named", "RAV4 欠点", ["rav4"]),
    ("RAV4の納期は今どのくらいですか？", "service", "named", "RAV4 納期", ["rav4"]),
    ("RAV4の値引き相場を教えてください。", "cost", "named", "RAV4 値引き", ["rav4"]),
    ("RAV4の中古相場と、旧型を買うときの注意点を教えてください。", "cost", "named", "RAV4 中古", ["rav4"]),
    ("カローラクロスの欠点は何ですか？", "brand", "named", "カローラクロス 欠点", ["corollacross"]),
    ("カローラクロスとヤリスクロスの違いは何ですか？どちらを選ぶべきですか？", "model", "named", "カローラクロス ヤリスクロス", ["corollacross", "yariscross"]),
    ("カローラクロスの納期は今どのくらいですか？", "service", "named", "カローラクロス 納期", ["corollacross"]),
    ("カローラクロスのグレードはどれがおすすめですか？", "model", "named", "カローラクロス グレード", ["corollacross"]),
    ("カローラクロスとヴェゼルを比較してください。", "model", "named", "カローラクロス ヴェゼル", ["corollacross"]),
    ("カローラクロスの値引き相場を教えてください。", "cost", "named", "カローラクロス 値引き", ["corollacross"]),
]:
    q(t, c, d, k, cs, "suv_mid")

# カローラ（セダン・ツーリング・スポーツ）
for t, c, d, k, cs, sg in [
    ("今でも新車で買える手頃なセダンを教えてください。", "purchase", "sedan", "セダン 新車 安い", ["corolla"], "sedan_c"),
    ("社用車に使うセダンを、燃費と価格で比較してください。", "cost", "business", "社用車 セダン", ["corolla"], "sedan_c"),
    ("運転のしやすいセダンはどれですか？初心者でも扱えますか？", "model", "beginner", "運転しやすい セダン", ["corolla"], "sedan_c"),
    ("ハイブリッドのセダンで、コスパが良いのはどれですか？", "eco", "cost", "ハイブリッド セダン コスパ", ["corolla", "prius"], "sedan_c"),
    ("教習車に使われている車は何ですか？運転を習った車に乗りたいです。", "model", "beginner", "教習車 車種", ["corolla"], "sedan_c"),
    ("50代の夫婦が乗る、乗り心地と燃費の良いセダンを教えてください。", "purchase", "senior", "セダン 50代", ["corolla"], "sedan_c"),
    ("長距離通勤に向く、燃費重視のセダンかワゴンを教えてください。", "eco", "commute", "長距離通勤 セダン ワゴン", ["corolla", "corollatouring"], "sedan_c"),
    ("ゴルフバッグが積めるセダンかワゴンでおすすめは？", "model", "leisure", "ゴルフバッグ 積める セダン", ["corolla", "corollatouring"], "sedan_c"),
    ("インプレッサと比較すべき車はどれですか？", "model", "compare", "インプレッサ ライバル", ["corollasport", "corolla"], "hatch_c"),
    ("世界で一番売れている車は何ですか？日本で買うならどう選べばいいですか？", "brand", "brand", "世界で一番売れている車", ["corolla"], "sedan_c"),
    ("セダンで燃費が一番いい車はどれですか？", "eco", "eco", "セダン 燃費", ["corolla", "prius"], "sedan_c"),
    ("家族4人で乗れる、運転しやすいセダンを教えてください。", "purchase", "family", "セダン 家族", ["corolla"], "sedan_c"),
    ("家族で使えるステーションワゴンを国産で選ぶなら何がいいですか？", "purchase", "family", "ステーションワゴン 国産", ["corollatouring"], "wagon"),
    ("ステーションワゴンが少なくなりましたが、いま新車で買える選択肢を教えてください。", "model", "wagon", "ステーションワゴン 新車", ["corollatouring", "crownestate"], "wagon"),
    ("レヴォーグと比べるべきステーションワゴンはどれですか？", "model", "compare", "レヴォーグ ライバル", ["corollatouring"], "wagon"),
    ("荷物がたくさん積めて燃費も良い、背の低い車を教えてください。", "model", "utility", "荷物 積める 燃費 車", ["corollatouring"], "wagon"),
    ("車中泊ができるステーションワゴンはありますか？", "model", "leisure", "ステーションワゴン 車中泊", ["corollatouring", "crownestate"], "wagon"),
    ("ハイブリッドのワゴン車で、価格が手頃なのはどれですか？", "eco", "cost", "ハイブリッド ワゴン", ["corollatouring"], "wagon"),
    ("犬を乗せるのに便利なワゴン車を教えてください。", "purchase", "pet", "犬 ワゴン 車", ["corollatouring"], "wagon"),
    ("自転車を積めるワゴン車を教えてください。", "model", "leisure", "自転車 積める ワゴン", ["corollatouring"], "wagon"),
    ("4WDのステーションワゴンで、雪国でも使えるのはどれですか？", "model", "snow", "4WD ステーションワゴン", ["corollatouring"], "wagon"),
    ("SUVではなく、背の低い車で荷室の広いものを探しています。おすすめは？", "purchase", "utility", "荷室 広い 低い車", ["corollatouring"], "wagon"),
    ("走りも楽しめるハッチバックの国産車を教えてください。", "model", "sport", "ハッチバック 走り", ["corollasport"], "hatch_c"),
    ("MAZDA3と比較すべきハッチバックはどれですか？", "model", "compare", "MAZDA3 ライバル", ["corollasport"], "hatch_c"),
    ("シビックの対抗になる国産ハッチバックを教えてください。", "model", "compare", "シビック ライバル", ["corollasport"], "hatch_c"),
    ("20代が乗っても様になる、スポーティなハッチバックは？", "purchase", "design", "スポーティ ハッチバック", ["corollasport"], "hatch_c"),
    ("ハイブリッドで走りも楽しい車はありますか？", "eco", "sport", "ハイブリッド 走り 楽しい", ["corollasport", "prius"], "hatch_c"),
    ("欧州車のようなデザインの国産ハッチバックはありますか？", "model", "design", "欧州 デザイン ハッチバック", ["corollasport"], "hatch_c"),
    ("フォルクスワーゲン ゴルフと比べられる国産車はどれですか？", "model", "compare", "ゴルフ 比較 国産", ["corollasport"], "hatch_c"),
    ("6速マニュアル（MT）で乗れる、普通のハッチバックはありますか？", "model", "sport", "MT ハッチバック", ["corollasport", "yaris"], "hatch_c"),
    ("通勤にも週末のドライブにも使える、大きすぎない車を教えてください。", "purchase", "commute", "通勤 ドライブ 車", ["corollasport"], "hatch_c"),
    ("2人暮らしで、たまに4人乗る程度です。運転の楽しい普通車は？", "purchase", "couple", "2人暮らし 車", ["corollasport"], "hatch_c"),
    ("カローラ（セダン）の評価はどうですか？", "model", "named", "カローラ セダン 評価", ["corolla"], "sedan_c"),
    ("カローラとプリウスはどちらがいいですか？", "model", "named", "カローラ プリウス", ["corolla", "prius"], "sedan_c"),
    ("カローラの値引き相場を教えてください。", "cost", "named", "カローラ 値引き", ["corolla"], "sedan_c"),
    ("カローラツーリングの荷室の広さはどのくらいですか？", "model", "named", "カローラツーリング 荷室", ["corollatouring"], "wagon"),
    ("カローラツーリングとレヴォーグを比較してください。", "model", "named", "カローラツーリング レヴォーグ", ["corollatouring"], "wagon"),
    ("カローラツーリングの欠点は何ですか？", "brand", "named", "カローラツーリング 欠点", ["corollatouring"], "wagon"),
    ("カローラスポーツの評価はどうですか？", "model", "named", "カローラスポーツ 評価", ["corollasport"], "hatch_c"),
    ("カローラスポーツとGRカローラの違いは何ですか？", "model", "named", "カローラスポーツ GRカローラ", ["corollasport"], "hatch_c"),
    ("カローラスポーツの中古は狙い目ですか？", "cost", "named", "カローラスポーツ 中古", ["corollasport"], "hatch_c"),
]:
    q(t, c, d, k, cs, sg)

# クラウン（4つの型）
for t, c, d, k, cs in [
    ("国産の高級セダンを教えてください。", "purchase", "luxury", "高級セダン 国産", ["crown"]),
    ("経営者が乗っている国産車は何が多いですか？", "brand", "business", "社長 車 国産", ["crown", "century"]),
    ("会社の役員車をセダンで選ぶなら、何がいいですか？", "purchase", "business", "役員車 セダン", ["crown", "century"]),
    ("燃料電池（水素）で走るセダンはありますか？", "eco", "ev", "水素 セダン", ["crown", "mirai"]),
    ("後席の乗り心地が良いセダンを、国産で教えてください。", "model", "comfort", "後席 乗り心地 セダン", ["crown"]),
    ("親の代から憧れの国産車といえば何ですか？今買うならどれですか？", "brand", "brand", "憧れ 国産車", ["crown"]),
    ("メルセデスのEクラスやBMWの5シリーズに対抗できる国産セダンはありますか？", "model", "compare", "Eクラス 対抗 国産", ["crown"]),
    ("800万円前後で買える国産の高級車を教えてください。", "cost", "luxury", "800万 高級車 国産", ["crown", "crownestate"]),
    ("高級感のあるSUVを国産で選ぶなら何がいいですか？", "purchase", "luxury", "高級 SUV 国産", ["crownsport", "harrier"]),
    ("スポーティなデザインの国産SUVでおすすめは？", "model", "design", "スポーティ SUV", ["crownsport"]),
    ("600万円前後で買える国産の上質な車を教えてください。", "cost", "luxury", "600万 車 国産", ["crownsport", "crowncrossover", "crownestate"]),
    ("セダンとSUVの中間のような車はありますか？", "model", "design", "セダン SUV 中間", ["crowncrossover"]),
    ("車高が少し高めで乗り降りが楽な、上級のセダンはありますか？", "model", "senior", "車高 高い セダン", ["crowncrossover"]),
    ("大人4人で長距離を快適に移動できる上質な車を教えてください。", "model", "comfort", "長距離 快適 車 上質", ["crownestate", "crowncrossover"]),
    ("ステーションワゴンとSUVのいいとこ取りの車はありますか？", "model", "wagon", "ワゴン SUV いいとこ取り", ["crownestate"]),
    ("CX-80と比較すべき国産車はどれですか？", "model", "compare", "CX-80 ライバル", ["crownestate"]),
    ("50代で初めて高級車を買うなら、国産で何を選びますか？", "purchase", "luxury", "50代 高級車", ["crown", "crowncrossover", "crownsport"]),
    ("レクサスまでは手が届かないけれど、上質な国産車に乗りたいです。何がいいですか？", "purchase", "luxury", "レクサス 手が届かない", ["crowncrossover", "crownsport", "harrier"]),
    ("プラグインハイブリッドの国産高級車には何がありますか？", "eco", "ev", "PHEV 高級車 国産", ["crownsport", "crownestate"]),
    ("CX-60と比べるべき国産SUVはどれですか？", "model", "compare", "CX-60 ライバル", ["crownsport", "harrier"]),
    ("メルセデスのGLCと比べられる国産SUVはありますか？", "model", "compare", "GLC 国産 比較", ["crownsport", "harrier"]),
    ("夫婦2人で乗る、デザイン重視の上質な車を教えてください。", "purchase", "couple", "夫婦 デザイン 車 上質", ["crownsport", "crowncrossover"]),
    ("乗り降りしやすい上級車を、60代の親に勧めたいです。何がいいですか？", "purchase", "senior", "乗り降り 上級車 60代", ["crowncrossover"]),
    ("セダンの乗り心地で、SUVのように視界が高い車はありますか？", "model", "comfort", "セダン 視界 高い", ["crowncrossover"]),
    ("ハイブリッドで走りも良い、大きめのセダン系の車は？", "model", "sport", "大きめ セダン ハイブリッド", ["crowncrossover", "crown"]),
    ("アコードと比べるべき国産車はどれですか？", "model", "compare", "アコード ライバル", ["crowncrossover", "crown"]),
    ("大人っぽいデザインで、雪道にも強い4WDの上級車は？", "model", "snow", "上級車 4WD", ["crowncrossover", "crownestate"]),
    ("レガシィ アウトバックの後継になる車を探しています。候補は？", "model", "compare", "アウトバック 後継", ["crownestate"]),
    ("荷室が広くて高級感もある車を、SUVかワゴンで教えてください。", "model", "utility", "荷室 広い 高級", ["crownestate"]),
    ("ボルボのXC60やV90と比べられる国産車はありますか？", "model", "compare", "ボルボ 比較 国産", ["crownestate"]),
    ("クラウンには何種類ありますか？それぞれの違いと選び方を教えてください。", "model", "named", "クラウン 種類 違い", ["crown"]),
    ("クラウンセダンの評価はどうですか？", "model", "named", "クラウンセダン 評価", ["crown"]),
    ("クラウンスポーツの欠点は何ですか？", "brand", "named", "クラウンスポーツ 欠点", ["crownsport"]),
    ("クラウンスポーツとハリアーを比較してください。", "model", "named", "クラウンスポーツ ハリアー", ["crownsport", "harrier"]),
    ("クラウンクロスオーバーの評価はどうですか？欠点も教えてください。", "model", "named", "クラウンクロスオーバー 評価", ["crowncrossover"]),
    ("クラウンクロスオーバーの値引き相場を教えてください。", "cost", "named", "クラウンクロスオーバー 値引き", ["crowncrossover"]),
    ("クラウンエステートの荷室は広いですか？車中泊はできますか？", "model", "named", "クラウンエステート 荷室", ["crownestate"]),
    ("クラウンエステートの評価はどうですか？", "model", "named", "クラウンエステート 評価", ["crownestate"]),
    ("クラウンの納期は今どのくらいですか？", "service", "named", "クラウン 納期", ["crown"]),
]:
    q(t, c, d, k, cs, "premium")

# プリウス
for t, c, d, k, cs in [
    ("デザインがかっこいいハイブリッド車を教えてください。", "purchase", "design", "かっこいい ハイブリッド車", ["prius"]),
    ("プラグインハイブリッドで、手頃な価格の車はどれですか？", "eco", "ev", "PHEV 安い", ["prius"]),
    ("自宅で充電できますが、EVには不安があります。どんな車がいいですか？", "eco", "ev", "EV 不安 PHEV", ["prius"]),
    ("長距離をよく走るので、燃費の良いハイブリッドの普通車を探しています。", "eco", "commute", "長距離 ハイブリッド 燃費", ["prius", "corolla"]),
    ("シビックのハイブリッドと比べるべき車はどれですか？", "model", "compare", "シビック ハイブリッド 比較", ["prius", "corollasport"]),
    ("低く構えたスポーティなデザインで、燃費も良い車を選ぶなら？", "model", "design", "スポーティ 燃費 車", ["prius"]),
    ("タクシーやカーシェアでよく見るハイブリッド車を、個人で買うのはありですか？", "purchase", "practical", "タクシー 車種 個人", ["prius"]),
    ("ハイブリッド車の元祖といわれる車の、今の評価を教えてください。", "brand", "brand", "ハイブリッド 元祖", ["prius"]),
    ("燃費性能で車を選ぶなら、どの車が一番ですか？", "eco", "eco", "燃費 性能 車", ["prius", "yaris"]),
    ("給電機能つきのプラグインハイブリッド車で、普段使いしやすい車を教えてください。", "eco", "disaster", "給電 PHEV", ["prius"]),
    ("300万円台のハイブリッド車で、走りもデザインも良いのは？", "purchase", "design", "300万 ハイブリッド", ["prius"]),
    ("若い世代に人気のハイブリッド車を教えてください。", "purchase", "design", "若者 人気 ハイブリッド", ["prius"]),
    ("プリウスの欠点は何ですか？後席や視界はどうですか？", "brand", "named", "プリウス 欠点", ["prius"]),
    ("プリウスのPHEVとハイブリッドはどちらがいいですか？", "eco", "named", "プリウス PHEV", ["prius"]),
    ("プリウスの納期は今どのくらいですか？", "service", "named", "プリウス 納期", ["prius"]),
    ("プリウスの値引き相場を教えてください。", "cost", "named", "プリウス 値引き", ["prius"]),
    ("プリウスの中古は何年式が狙い目ですか？", "cost", "named", "プリウス 中古", ["prius"]),
    ("プリウスの実燃費はどのくらいですか？", "eco", "named", "プリウス 実燃費", ["prius"]),
]:
    q(t, c, d, k, cs, "hybrid")

# 電動車（bZ4X・MIRAI）
for t, c, d, k, cs in [
    ("国産の電気自動車（EV）でおすすめはどれですか？", "purchase", "ev", "国産 EV おすすめ", ["bz4x"]),
    ("日産リーフと比べるべきEVはどれですか？", "model", "compare", "リーフ 比較 EV", ["bz4x"]),
    ("SUVタイプのEVを国産で選ぶなら何がいいですか？", "purchase", "ev", "EV SUV 国産", ["bz4x"]),
    ("テスラ以外でEVを買うなら、何が候補になりますか？", "purchase", "ev", "テスラ以外 EV", ["bz4x"]),
    ("EVの補助金を使うと、実質いくらで買えますか？おすすめの車種も教えてください。", "cost", "ev", "EV 補助金", ["bz4x"]),
    ("自宅に充電器を付けてEVに乗りたいです。航続距離の長い国産EVはどれですか？", "model", "ev", "EV 航続距離", ["bz4x"]),
    ("寒い地域でEVに乗るのは現実的ですか？向いている車種はありますか？", "model", "ev", "寒冷地 EV", ["bz4x"]),
    ("BYDのEVが安いと聞きました。国産のEVと比べるとどうですか？", "model", "compare", "BYD 比較", ["bz4x"]),
    ("日産アリアと比較すべきEVはどれですか？", "model", "compare", "アリア 比較", ["bz4x"]),
    ("4WDの電気自動車で、雪道やアウトドアにも使えるのは？", "model", "ev", "EV 4WD", ["bz4x"]),
    ("水素で走る燃料電池車（FCV）は、今でも新車で買えますか？", "eco", "ev", "燃料電池車 買える", ["mirai", "crown"]),
    ("水素ステーションの近くに住んでいます。燃料電池車に乗る価値はありますか？", "eco", "ev", "水素 車 価値", ["mirai"]),
    ("補助金が手厚いエコカーを教えてください。", "cost", "ev", "補助金 エコカー", ["mirai", "bz4x"]),
    ("CO2を出さない車に乗りたいです。EVと燃料電池車ならどちらがいいですか？", "eco", "ev", "EV FCV 比較", ["bz4x", "mirai"]),
    ("中古で安く買える燃料電池車はありますか？", "cost", "used", "燃料電池車 中古", ["mirai"]),
    ("静かで滑らかに走る、高級感のあるエコカーを教えてください。", "model", "comfort", "静か エコカー 高級", ["mirai", "bz4x"]),
    ("ヒョンデのネッソと比べるべき燃料電池車はありますか？", "model", "compare", "ネッソ 比較", ["mirai"]),
    ("会社で次世代エコカーを導入したいです。EVと燃料電池車の選び方を教えてください。", "purchase", "business", "法人 EV FCV", ["mirai", "bz4x"]),
    ("水素自動車のメリットとデメリットを、具体的な車種で教えてください。", "model", "ev", "水素自動車 メリット", ["mirai"]),
    ("走行中にCO2を出さない車で、長距離も走れるのはどれですか？", "model", "ev", "ゼロエミッション 長距離", ["mirai", "bz4x"]),
    ("災害時に家へ電気を供給できる車を教えてください。", "eco", "disaster", "車 給電 家", ["mirai", "bz4x", "prius"]),
    ("bZ4Xの航続距離は実際どのくらいですか？", "model", "named", "bZ4X 航続距離", ["bz4x"]),
    ("bZ4Xの欠点は何ですか？", "brand", "named", "bZ4X 欠点", ["bz4x"]),
    ("bZ4Xは補助金込みでいくらで買えますか？", "cost", "named", "bZ4X 補助金", ["bz4x"]),
    ("bZ4Xと日産アリアを比較してください。", "model", "named", "bZ4X アリア", ["bz4x"]),
    ("MIRAIの中古が安いのはなぜですか？", "cost", "named", "MIRAI 中古", ["mirai"]),
    ("MIRAIの維持費（水素代）はどのくらいですか？", "cost", "named", "MIRAI 維持費", ["mirai"]),
    ("MIRAIの評価はどうですか？買って後悔しませんか？", "brand", "named", "MIRAI 評価", ["mirai"]),
]:
    q(t, c, d, k, cs, "ev")

# ヴェルファイア
for t, c, d, k, cs in [
    ("走りの良さも重視した高級ミニバンはありますか？", "model", "sport", "高級ミニバン 走り", ["vellfire"]),
    ("黒を基調にした迫力のあるデザインの高級ミニバンは？", "model", "design", "迫力 ミニバン", ["vellfire"]),
    ("ターボエンジンの大型ミニバンはありますか？", "model", "power", "ターボ ミニバン", ["vellfire"]),
    ("リセールの良い高級ミニバンを教えてください。", "cost", "resale", "高級ミニバン リセール", ["vellfire"]),
    ("1,000万円近い予算で選ぶ、運転も楽しい大型ミニバンは？", "purchase", "luxury", "1000万 ミニバン", ["vellfire"]),
    ("レクサスLMと比べるべきミニバンはどれですか？", "model", "compare", "レクサスLM 比較", ["vellfire"]),
    ("家族用と社用を兼ねる大型ミニバン、何がいいですか？", "purchase", "business", "社用 ミニバン 大型", ["vellfire"]),
    ("エルグランドと比較すべき大型ミニバンを教えてください。", "model", "compare", "エルグランド ライバル", ["vellfire"]),
    ("デリカD:5と迷っています。ほかに比べるべきミニバンは？", "model", "compare", "デリカD:5 比較", ["vellfire"]),
    ("2列目が最も豪華な7人乗りの国産車はどれですか？", "model", "luxury", "2列目 豪華 7人乗り", ["vellfire"]),
    ("プラグインハイブリッドのミニバンはありますか？", "eco", "ev", "PHEV ミニバン", ["vellfire"]),
    ("オデッセイと比べるべき上級ミニバンはどれですか？", "model", "compare", "オデッセイ 比較", ["vellfire"]),
    ("ヴェルファイアの欠点は何ですか？", "brand", "named", "ヴェルファイア 欠点", ["vellfire"]),
    ("ヴェルファイアのPHEVの評価はどうですか？", "eco", "named", "ヴェルファイア PHEV", ["vellfire"]),
    ("ヴェルファイアの納期は今どのくらいですか？", "service", "named", "ヴェルファイア 納期", ["vellfire"]),
    ("ヴェルファイアのリセールバリューはどのくらいですか？", "cost", "named", "ヴェルファイア リセール", ["vellfire"]),
    ("ヴェルファイアの値引き相場を教えてください。", "cost", "named", "ヴェルファイア 値引き", ["vellfire"]),
]:
    q(t, c, d, k, cs, "minivan_l")

# GR86
for t, c, d, k, cs in [
    ("300万円台で買えるスポーツカーを教えてください。", "purchase", "sport", "スポーツカー 300万", ["gr86"]),
    ("FR（後輪駆動）の新車で、手頃なスポーツカーは？", "model", "sport", "FR スポーツカー 安い", ["gr86"]),
    ("MTで楽しめる国産のスポーツカーを教えてください。", "model", "sport", "MT スポーツカー 国産", ["gr86"]),
    ("ロードスターと比較すべきスポーツカーはどれですか？", "model", "compare", "ロードスター ライバル", ["gr86"]),
    ("4人乗れるスポーツカーはありますか？", "model", "family", "4人乗り スポーツカー", ["gr86"]),
    ("サーキット走行を始めたいです。入門に向く車を教えてください。", "purchase", "sport", "サーキット 入門 車", ["gr86"]),
    ("若いうちに乗っておきたい国産スポーツカーは？", "purchase", "sport", "若いうち スポーツカー", ["gr86"]),
    ("スバルのBRZと、その兄弟車の違いを教えてください。", "model", "compare", "BRZ 兄弟車", ["gr86"]),
    ("中古で200万円以下で買えるスポーツカーを教えてください。", "cost", "used", "中古 スポーツカー 200万", ["gr86"]),
    ("趣味のセカンドカーにスポーツカーを買うなら何がいいですか？", "purchase", "hobby", "セカンドカー スポーツカー", ["gr86"]),
    ("維持費が安めのスポーツカーはありますか？", "cost", "cost", "スポーツカー 維持費", ["gr86"]),
    ("フェアレディZと迷っています。ほかに候補はありますか？", "model", "compare", "フェアレディZ 比較", ["gr86"]),
    ("GR86の欠点は何ですか？", "brand", "named", "GR86 欠点", ["gr86"]),
    ("GR86とBRZの違いは何ですか？", "model", "named", "GR86 BRZ 違い", ["gr86"]),
    ("GR86の納期は今どのくらいですか？", "service", "named", "GR86 納期", ["gr86"]),
    ("GR86の中古相場を教えてください。", "cost", "named", "GR86 中古", ["gr86"]),
]:
    q(t, c, d, k, cs, "sports")

# ハイラックス
for t, c, d, k, cs in [
    ("日本で買えるピックアップトラックには何がありますか？", "model", "pickup", "ピックアップトラック 日本", ["hilux"]),
    ("荷台付きで、普段使いもできる車を教えてください。", "model", "utility", "荷台 普段使い 車", ["hilux"]),
    ("釣りやキャンプの道具を荷台に積める車を探しています。", "purchase", "outdoor", "荷台 キャンプ 車", ["hilux"]),
    ("1ナンバー（貨物登録）の車で、趣味にも使えるものはありますか？", "cost", "hobby", "1ナンバー 趣味", ["hilux"]),
    ("トライトンと比較すべきピックアップトラックは？", "model", "compare", "トライトン ライバル", ["hilux"]),
    ("仕事と趣味を兼ねる、頑丈で長く乗れる4WDを教えてください。", "purchase", "work", "頑丈 4WD 仕事", ["hilux"]),
    ("軽トラより大きく、乗用にも使えるトラックはありますか？", "model", "work", "軽トラ 大きい 乗用", ["hilux"]),
    ("ディーゼルの4WDで、荷物をたくさん積める車を教えてください。", "model", "work", "ディーゼル 4WD 荷物", ["hilux"]),
    ("アメリカのようなピックアップに乗りたいです。日本で現実的な選択肢は？", "purchase", "design", "アメ車 ピックアップ 日本", ["hilux"]),
    ("サーフボードやバイクを積める車を教えてください。", "model", "hobby", "バイク 積める 車", ["hilux"]),
    ("雪国の現場仕事で使う、悪路に強いトラックは？", "model", "work", "悪路 トラック 雪", ["hilux"]),
    ("キャンピングシェルを載せられる車はありますか？", "model", "outdoor", "キャンピングシェル 車", ["hilux"]),
    ("ハイラックスの維持費はどのくらいですか？1ナンバーの税金や車検も教えてください。", "cost", "named", "ハイラックス 維持費", ["hilux"]),
    ("ハイラックスの欠点は何ですか？", "brand", "named", "ハイラックス 欠点", ["hilux"]),
    ("ハイラックスの納期は今どのくらいですか？", "service", "named", "ハイラックス 納期", ["hilux"]),
    ("ハイラックスのGRスポーツはどんな車ですか？", "model", "named", "ハイラックス GRスポーツ", ["hilux"]),
]:
    q(t, c, d, k, cs, "pickup")

# センチュリー
for t, c, d, k, cs in [
    ("日本で最高級の車は何ですか？", "brand", "luxury", "日本 最高級 車", ["century"]),
    ("皇室や政府の公用車に使われている車は何ですか？", "brand", "official", "公用車 車種", ["century"]),
    ("国産のショーファーカー（運転手付きで乗る車）を教えてください。", "model", "chauffeur", "ショーファーカー 国産", ["century", "crown"]),
    ("大企業の社長車として使われる国産車は何ですか？", "brand", "business", "社長車 国産", ["century", "crown"]),
    ("2,000万円以上する国産車には何がありますか？", "cost", "luxury", "2000万 国産車", ["century"]),
    ("ロールス・ロイスやマイバッハに対抗できる日本車はありますか？", "model", "compare", "ロールスロイス 対抗 日本車", ["century"]),
    ("SUV型のショーファーカーはありますか？", "model", "chauffeur", "SUV ショーファーカー", ["century"]),
    ("一番高い国産車は何ですか？", "cost", "luxury", "一番高い 国産車", ["century"]),
    ("VIPの送迎に使う最上級の車を、国産で選ぶなら？", "purchase", "chauffeur", "VIP 送迎 車 国産", ["century"]),
    ("レクサスLSより上のクラスの国産車はありますか？", "model", "compare", "レクサスLS 上", ["century"]),
    ("職人の手作業で作られている日本の車はありますか？", "brand", "craft", "手作り 車 日本", ["century"]),
    ("後席に乗る人のための車として、最も快適な国産車は？", "model", "comfort", "後席 快適 国産", ["century", "crown"]),
    ("センチュリー（SUV）の価格と評価を教えてください。", "model", "named", "センチュリー SUV", ["century"]),
    ("センチュリーのセダンとSUVの違いは何ですか？", "model", "named", "センチュリー セダン SUV", ["century"]),
    ("センチュリーは個人でも買えますか？", "purchase", "named", "センチュリー 個人", ["century"]),
]:
    q(t, c, d, k, cs, "chauffeur")

# 商用バン（プロボックス・ハイエース）
for t, c, d, k, cs in [
    ("営業車に使うライトバンで、燃費と耐久性の良いのはどれですか？", "cost", "business", "ライトバン 営業車", ["probox"]),
    ("会社の営業車をまとめて入れ替えます。おすすめの商用バンを教えてください。", "purchase", "business", "営業車 入れ替え 商用バン", ["probox"]),
    ("荷物が積める仕事用の小型バン（4ナンバー）でおすすめは？", "model", "work", "4ナンバー 小型バン", ["probox"]),
    ("個人事業主の配達や仕事用に、安くて丈夫な車を教えてください。", "purchase", "work", "個人事業主 車 丈夫", ["probox", "hiacevan"]),
    ("ハイブリッドの商用バンはありますか？", "eco", "business", "ハイブリッド 商用バン", ["probox"]),
    ("ADバンと比較すべき商用車はどれですか？", "model", "compare", "ADバン ライバル", ["probox"]),
    ("仕事用のワンボックスバンでおすすめはどれですか？", "purchase", "work", "ワンボックスバン 仕事", ["hiacevan"]),
    ("大工や職人に人気の商用バンは何ですか？", "brand", "work", "職人 人気 バン", ["hiacevan", "probox"]),
    ("キャンピングカーのベース車として人気の車は？", "model", "outdoor", "キャンピングカー ベース車", ["hiacevan"]),
    ("送迎用に10人以上乗れる車はありますか？", "model", "business", "10人乗り 車", ["hiacevan"]),
    ("キャラバンと比較すべきワンボックスは？", "model", "compare", "キャラバン ライバル", ["hiacevan"]),
    ("車中泊仕様に改造しやすいバンを教えてください。", "model", "leisure", "車中泊 バン 改造", ["hiacevan"]),
    ("大きな荷物を運ぶのに便利なバンを教えてください。", "model", "work", "大きい荷物 バン", ["hiacevan"]),
    ("ディーゼルの商用バンで、長く乗れるのはどれですか？", "model", "work", "ディーゼル 商用バン", ["hiacevan"]),
    ("営業車をプライベートでも使いたいです。乗用としても快適な商用車は？", "purchase", "business", "商用車 乗用 快適", ["probox"]),
    ("NV200バネットと比べるべき商用バンは？", "model", "compare", "NV200 比較", ["probox", "hiacevan"]),
    ("社用車のコストを下げたいです。燃費と維持費の安い仕事用の車は？", "cost", "business", "社用車 コスト 削減", ["probox"]),
    ("リセールが落ちにくい商用車はありますか？", "cost", "resale", "商用車 リセール", ["hiacevan"]),
    ("福祉施設の送迎で、車椅子を乗せられる車は？", "purchase", "welfare", "福祉 送迎 車椅子 車", ["hiacevan"]),
    ("釣りやサーフィンの道具を積む、趣味用のバンを教えてください。", "purchase", "hobby", "趣味 バン", ["hiacevan", "probox"]),
    ("プロボックスの燃費はどのくらいですか？ハイブリッドは得ですか？", "eco", "named", "プロボックス 燃費", ["probox"]),
    ("プロボックスの欠点は何ですか？", "brand", "named", "プロボックス 欠点", ["probox"]),
    ("プロボックスを自家用で乗るのはありですか？", "purchase", "named", "プロボックス 自家用", ["probox"]),
    ("ハイエースの納期は今どのくらいですか？", "service", "named", "ハイエース 納期", ["hiacevan"]),
    ("ハイエースのリセールバリューはどのくらいですか？", "cost", "named", "ハイエース リセール", ["hiacevan"]),
    ("ハイエースが盗難されやすいと聞きます。対策を教えてください。", "safety", "named", "ハイエース 盗難", ["hiacevan"]),
    ("ハイエースのバンとワゴンの違いは何ですか？", "model", "named", "ハイエース バン ワゴン", ["hiacevan"]),
]:
    q(t, c, d, k, cs, "van")

# 安全の観点（各型に少しずつ）と、プロボックスの追加
q("安全性能の評価が高いSUVを教えてください。最新の評価で比較したいです。", "safety", "safety", "SUV 安全性能 評価", ["harrier", "rav4", "corollacross"], "suv_mid")
q("子供を乗せるので、衝突安全性の高いSUVを選びたいです。どれがいいですか？", "safety", "family", "衝突安全 SUV", ["rav4", "harrier"], "suv_mid")
q("高速道路の運転支援（渋滞時の追従など）が優秀なセダンを教えてください。", "safety", "safety", "運転支援 セダン", ["crown", "corolla"], "premium")
q("駐車を自動でしてくれる機能が優秀な上級車を教えてください。", "safety", "tech", "駐車支援 上級車", ["crown", "crownsport", "crowncrossover"], "premium")
q("外回りの営業で毎日長い距離を走ります。丈夫で荷物も積める仕事用の車は？", "purchase", "business", "営業 車 丈夫", ["probox"], "van")


# ---------------------------------------------------------------- 車名の照合（run_car.detect_cars と同じ手順）
def detect(text: str, catalog: list[dict]) -> dict[str, int]:
    work = text
    tokens = [(a, c["id"]) for c in catalog for a in c["aliases"]]
    tokens += [(g, None) for c in catalog for g in (c.get("guards") or [])]
    tokens.sort(key=lambda x: (-len(x[0]), x[1] is None))
    found: dict[str, int] = {}
    for tok, cid in tokens:
        start = 0
        while True:
            i = work.find(tok, start)
            if i < 0:
                break
            if cid is not None and (cid not in found or i < found[cid]):
                found[cid] = i
            work = work[:i] + "＊" * len(tok) + work[i + len(tok):]
            start = i + len(tok)
    return found


def main() -> None:
    old = yaml.safe_load(open(ROOT / "config/cars.yaml", encoding="utf-8"))
    new_ids = {c["id"] for c in NEW_FOCUS}
    focus = []
    for c in old["focus"]:
        c = dict(c); c["id"] = OLD2NEW.get(c["id"], c["id"])
        c["rivals_own"] = [OLD2NEW.get(x, x) for x in c.get("rivals_own") or []]
        focus.append(c)
    focus += [dict(c) for c in NEW_FOCUS]
    assert len(focus) == 29, len(focus)
    rivals = [dict(r) for r in old["rivals"] if r["id"] not in new_ids]          # vellfire・yaris・yariscross・rav4 は29車種の側へ
    have = {r["id"] for r in rivals} | {c["id"] for c in focus}
    for rid, nm, br, al, gd in NEW_RIVALS:
        assert rid not in have, rid
        r = {"id": rid, "name": nm, "brand": br, "aliases": al}
        if gd: r["guards"] = gd
        rivals.append(r); have.add(rid)
    for c in focus:
        for x in (c.get("rivals") or []) + (c.get("rivals_own") or []):
            assert x in have, (c["id"], x)
    cfg = {"version": 1, "updated_on": TODAY,
           "note": "①車種別AI分析の29車種版（tier=car29）。tools/car29_build.py で作る。元の8車種（cars.yaml）は変えない。",
           "segs": {**old["segs"], **SEGS_NEW}, "focus": focus, "rivals": rivals}
    focus_cat = [{"id": c["id"], "aliases": c["aliases"], "guards": c.get("guards") or []} for c in focus]
    all_cat = focus_cat + [{"id": r["id"], "aliases": r["aliases"], "guards": r.get("guards") or []} for r in rivals]

    # 元の8車種の質問を写す
    reg = yaml.safe_load(open(ROOT / "prompts/registry.yaml", encoding="utf-8"))["prompts"]
    olds = [p for p in reg if p.get("tier") == "car"]
    out = []
    for p in olds:
        p = dict(p)
        p["cars"] = [OLD2NEW.get(x, x) for x in p.get("cars") or []]
        p["active_for"] = [OLD2NEW.get(x, x) for x in p.get("active_for") or []]
        p["named_cars"] = sorted(detect(p["text"], all_cat).keys() & {c["id"] for c in focus})
        p["tier"] = "car29"; p["source_tier"] = "car"
        out.append(p)
    # 新しい21車種の質問
    seen = set(p["text"] for p in out)
    for i, x in enumerate(Q, 1):
        assert x["text"] not in seen, x["text"]; seen.add(x["text"])
        for cid in x["cars"]:
            assert cid in new_ids, (cid, x["text"])
        named = sorted(detect(x["text"], all_cat).keys() & {c["id"] for c in focus})
        out.append({"id": f"w{i:03d}", "text": x["text"], "category": x["category"], "driver": x["driver"], "brand": False,
                    "source": "car29_v1", "tier": "car29", "added_on": TODAY, "tier_since": TODAY, "demand": None, "volume": 0,
                    "fanout_hits": 0, "ugc_hits": 0, "growth": 1.0, "keyword": x["keyword"], "demand_history": [],
                    "cars": x["cars"], "named_cars": named, "seg": x["seg"], "active_for": x["cars"]})

    # 車種ごとの、出現率を測る質問の数（車名を含まない質問）
    n_gen = {c["id"]: sum(1 for p in out if c["id"] in p["cars"] and c["id"] not in p["named_cars"]) for c in focus}
    low = {k: v for k, v in n_gen.items() if v < 10}
    assert not low, low
    yaml.safe_dump(cfg, open(ROOT / "config/cars29.yaml", "w", encoding="utf-8"), allow_unicode=True, sort_keys=False, width=200)
    with open(ROOT / "prompts/car29.yaml", "w", encoding="utf-8") as f:
        f.write("# 29車種版の車種別AI分析（tier=car29）の質問。tools/car29_build.py で作る（手で直さない）。\n")
        yaml.safe_dump({"version": 1, "updated_at": TODAY, "prompts": out}, f, allow_unicode=True, sort_keys=False, width=200)
    print(f"focus {len(focus)} / rivals {len(rivals)} / 質問 {len(out)}（元の8車種 {len(olds)}・新しい21車種 {len(Q)}）")
    print("車種ごとの、出現率を測る質問の数:", dict(sorted(n_gen.items(), key=lambda x: x[1])))


if __name__ == "__main__":
    main()
