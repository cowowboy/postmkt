# Changelog

帶日期的變更紀錄從 README「快速接手」搬出集中於此（2026-07-24 起）；
更早的逐日歷史見 git log。常青的架構／口徑／教訓說明仍在 README。

## 2026-08-30 「本次 API 費用估算」補到摘要分析其餘場次（三站）

2026-08-27 首次上線的費用估算只掛在**單發**摘要分析（postmkt `691226a`／
taiwan-flow-live-v2 `737d9b2`／taiwan-stock-news `04c4d78`，當時明記「其餘場次未加」）。
本次補齊，**全部重用既有的三站同步函式 `insightCostText`，未產生任何變體**：

| 場次 | 位置 | 本次 |
|------|------|------|
| 單發摘要分析 | 三站 `insightHtml`／`renderInsight` | 08-27 已有 |
| 雲端歷史（單發存檔） | 三站 `cloudHistSectionHtml` | **補上**（`entry.model`＋`entry.usage` 皆在） |
| 彙總場 3 份原始分析 | postmkt `sumResultHtml` 的 `six[]` | **補上**（`s.model` 在 JSON 內） |
| 彙總場的彙總本身 | postmkt `sumResultHtml` 的 `synthesis` | **手動場補上**；自動場見下 |
| 持股診斷 AI 解讀 | postmkt `diagCardHtml` | **補上**（`ai.model`＋`ai.usage` 皆在） |

**未補的一項，照實記錄**：`data/summary/*.json`（GitHub Actions 自動彙總場）的
`synthesis` 只有 `{text, usage, via}`、**沒有 model 欄**。彙總模型雖在程式碼裡固定是
Opus 4.8，但那是「從程式碼推論」而非資料本身，硬套等於顯示一個猜的數字，故
`sumResultHtml` 在 `synthesis.model` 缺席時讓 `insightCostText` 回空字串、**寧可不顯示**。
要補齊需 `build_summary.py` 的 `write_output` 一併寫入彙總模型（本次未動 Python）。
手動彙總場已改成落地時寫入 `synthesis.model`（新增常數 `SUM_SYNTH_MODEL`，同時是
`runSummary` 呼叫點的事實來源，取代原本寫死的字面量），故手動場即刻可見。

**`insightCostText` 本體唯一的改動＝batch 半價**：自動彙總場走 Message Batches，其 `usage`
帶 `service_tier:"batch"`，而 Batch API 是標準價的 50%（claude-api skill 定價表）。不處理的話
自動場的每一份原始分析都會**高估一倍**。故加 `const disc = usage.service_tier === "batch" ? 0.5 : 1`
一行；瀏覽器單發的 `service_tier` 是 `"standard"`，走 `disc=1`，**既有單發場數字逐位元不變**
（實測 Sonnet 5／輸入 10,000／輸出 2,000＝`US$0.0400（≈ NT$1.3）`，改動前後相同）。
三站該區塊改後 sha256 仍全等（`5139f23e…`），新增的雲端歷史片段
`${insightCostText(u, cur.entry.model)}` 三站亦逐字相同。

`INSIGHT_PRICES`／`USD_TWD` 本次未動（08-30 剛對齊）。經清點，各場次用到的 model id 只有
`claude-sonnet-5`（`SUM_MODELS`、單發下拉）與 `claude-opus-4-8`（彙總、單發下拉），
**都在價目表內**，無「表中沒有的 model」情形；表外 model 的現行行為（回空字串、靜默）未改。

## 2026-08-30 輪動雷達日頻 RRG 錨點標名碰撞避讓

`rrgdSvg` 的錨點與加選鏈標名原本一律固定偏移（x+7、y−6），零碰撞偵測——點密集日會互相重疊，
只能靠 hover 兜底（README 原「未解／待觀察」條）。本次改為候選位搜尋：每個頭點一圈 8 個候選位
（第一個候選位在 gap=3 時恰為舊版的 (x+7, y−6)，故不擁擠時外觀不變），撞光再跑 gap 12/22/32
外三圈共 32 個，取第一個零重疊處；全撞則放在**加權重疊面積最小**處（`ovlp()` 算的是重疊面積
而非布林，就是為了讓 fallback 能挑最小者），全出界才夾回畫布內。**不省略任何標籤**——錨點與
加選鏈都是使用者主動要看的，靜默消失比疊放更糟。佔位權重＝當日成交額（`j.chains[nm].amt[t]`，
正規化 0~100），擁擠時優先蓋掉小鏈而非龍頭；優先序＝**加選鏈 → 錨點（成交額遞減）**，
加選是使用者主動點的故優先保障。標籤改為統一畫在所有頭點之上並加 `pointer-events="none"`，
不再遮住頭點的 hover `<title>`。

演算法移植自 taiwan-flow-live-v2 `index.html` 的 `ovRrgHtml` 標籤段。移植時重新對應三處語意：
①本頁頭點固定 r=5（盤中版是可變泡泡半徑）②權重改用成交額（盤中版用成交佔比）③強制標名族群＝
加選＋錨點（盤中版是錨點／改善象限／位移異常）。差異夠大，**兩邊刻意不登記為「改一邊要同步
另一邊」的跨站同步函式**，只在各自程式碼加互相引用的註解。

**量測**（臨時 Node 腳本抽 `index.html` 的 `rrgd*` 視覺層離線跑，未入 repo；序列取
taiwan-flow-live-v2 `data/chain_daily/series.json`，264 個可顯示交易日 × 15 個標籤＝3,960 個，
加選情境固定取當日成交額第 11~15 名）：

| 指標 | 改動前 | 改動後 |
|------|-------|-------|
| 標籤矩形兩兩重疊配對數 | 719 | **0** |
| 有重疊的標籤佔比 | 28.2%（1,117／3,960） | **0%** |
| 跑出畫布的標籤數 | 31 | **0** |

位置擾動：83.6% 的標籤與改動前逐位元相同，99.6% 落在第一圈（離頭點 ≤18px），只有 17 個
（0.4%）走到外圈，最遠一次位移 38px；加權疊放 fallback 全期間未觸發。優先序不變式亦逐日
驗過：314 組「舊版會相撞的 加選×錨點」配對中，沒有任何一組是錨點佔住位置而把加選擠開
（5 組表面例外經逐筆歸因，均為加選被**更前序的加選**擋住，非錨點所致）。

改動全部落在 `rrgdSvg` 的標籤輸出段，`RRGD-PURE-BEGIN … RRGD-PURE-END` 區塊逐位元未動
（raw 與去註解後 sha256 前後相同），故不觸發與 `taiwan-flow-live-v2/backtest/
run_rrg_daily_axes.py` 的前後端座標對拍不變式，無須重跑對拍。

## 2026-08-30 輪動雷達「動能領先」更名為「相對強度領先」＋三站 INSIGHT_PRICES 對齊

**UI 更名**：承接同日排序鍵改動（見下一則），領先象限候補清單的排序已由 RS-Momentum 改為
RS-Ratio，「動能領先」四個字與排序不符（momentum vs level），故標籤改為
**「相對強度領先（領先象限）」**。改動點只有 `renderRrgd` 的 `sec(...)` 一處（`index.html:1762`）
＋同段程式碼註解；「資金剛輪入（改善象限）」的名稱與排序（RS-Momentum）維持不變。
README 輪動雷達段的口徑敘述同步更新。

**INSIGHT_PRICES 三站對齊**：`claude-sonnet-5` 的價目在 postmkt 已由 `[3,15]` 更正為
`[2,10]`，但 taiwan-stock-news 與 taiwan-flow-live-v2 仍是 `[3,15]`（費用估算高估約 50%），
本次補齊。三站該常數逐字一致（sha256 比對），`insightCostText` 本體未動。同批把該區塊註解
「Sonnet 5 於 2026-08-31 前有 intro 價 $2/$10，此處採標準價 $3/$15」除鏽——該敘述與現值
`[2,10]` 自相矛盾且次日到期，改為敘明 claude-api skill 定價表（快取日 2026-06-24）所列現行價
Opus 4.8 $5/$25、Sonnet 5 $2/$10。`claude-opus-4-8`（`[5,25]`）與 `USD_TWD`（31.5）
三站原本即一致，無其他欄位差異。

## 2026-08-30 輪動雷達「動能領先」排序改用 RS-Ratio

「動能領先（領先象限）」候補清單原依 RS-Momentum 遞減排序，改為依 RS-Ratio 遞減。
理由有二：(1) 語意——該清單要表達的是相對強度的**水準**，RS-Momentum 是水準的變化率；
(2) 回測旁證——taiwan-flow-live-v2 `backtest/report_chain_overlap.md` **§2.5**（§5.3 複述）記載，
同一套橫斷面排序下 RS-Momentum 水準六種切法 6/6 為負（平均 -0.238%）、RS-Ratio 水準 6/6 為正
（平均 +0.156%）；純動能（過去 L 日超額報酬）18/18 點估計為正、4/18 分塊 CI 顯著則是 **§2.4**
（§5.3 一併複述）。**該報告自陳 CI 全跨 0（§2.5 表下方「分塊 CI 不跨 0 的格子數：0 / 12」）、
只是方向線索不是可上線結論**；且 §2.5 量的是「全 47 條鏈橫斷面排序、取前後 20%」的 T+3 多空
價差，與本清單「先過 N=3 持續性、只留領先象限的鏈，再排序」不是同一個構造，該節數字不能直接
推到象限內排序——故主要依據是 (1) 的語意，(2) 僅為方向旁證。本次只調整排序鍵，不改軸定義、
不改象限判定、不加任何買賣建議語氣。

實作：`rrgdPersistList` 新增 `sortKey` 參數（`RRGD_SORT_RATIO`／`RRGD_SORT_MOM`），
「資金剛輪入（改善象限）」維持 RS-Momentum；清單說明列的排序文字改為逐清單標示。
postmkt 無 RRG 後端實作（座標公式正本在 taiwan-flow-live-v2 `backtest/run_rrg_daily_axes.py`，
該處無此清單排序），故本次為單邊改動。UI 標籤「動能領先」暫未更名。

## 2026-08-12 新增第 13 個 tab「選股」（分析師預估 EPS 篩選）

動機＝富邦投顧「明年 EPS>50 找萬元股」報告的自動化重現。新管線 `src/build_screen.py` →
`data/screen/screen.json`：TradingView scanner 批次初篩（`earnings_per_share_forecast_next_fy>=20`，
server 端 filter，實篩 91 檔）→ 鉅亨網 `marketinfo.api.cnyes.com` 逐檔補 FactSet 共識
（多年度預估 EPS 高/低/均/中位＋分析師家數、目標價、券商評等；上市上櫃一律 `TWS:` 前綴，
節流 1.2s/檔，連續 10 檔全失敗才 abort 且 abort 不覆蓋舊檔）→ 合併 diag.json 的
pe/yoy/mom/rvs 欄。掛在 diag workflow 的 build_diag 之後（`continue-on-error: true`，
失敗不擋 diag 主產物；commit composite 加 `add_all` 容錯不存在路徑）。

前端 tab：`tbl()` 全欄排序、門檻鈕（明年 EPS ≥30/50/100，預設 50）、forward PER＝現價÷預估
EPS 自算、目標價中位與潛在漲幅。**年度欄錨定日曆年**（基準年＝資料日年份，今年/明年欄
分別取 est[Y]/est[Y+1]，缺該年度顯「—」）——初版曾用「逐列取最小年度＋欄頭取眾數」，
同一欄會混到不同年度（欄頭 FY25、2330 那格卻是 2026 值），驗收抓到後改錨定制。
門檻過濾口徑＝est[Y+1].mean 缺值 fallback TradingView feps。固定標注「FactSet（經鉅亨網）／
TradingView，預估值為券商共識非保證」。資料源皆非官方端點、無 SLA（Yahoo 2023 加 crumb
為前例），管線失敗時前端顯示舊檔或空狀態降級。離線測試 `tests/test_screen.py` 14 支
（fixture 免網路）；13 tab Playwright 零 console error、門檻筆數/PER/漲幅與 JSON 獨立重算零差異。

## 2026-08-11 新增第 12 個 tab「輪動雷達」（盤後日頻 RRG）

規格＝taiwan-flow-live-v2 `docs/rrg-daily-spec-20260811.md` §4 第三階段。軸與參數為該專案
第二階段定案：**B-ew**（價格版 RRG × 等權報酬）、z-score 窗 n=12、動能回看 k=10、候補清單
持續性 N=3；公式正本＝該站 `backtest/run_rrg_daily_axes.py` 的 `axis_systems`，前端
`RRGD-PURE` 區塊逐式重現並以 Node 對拍（抽 2026-08-11／2025-11-28／2026-04-13 三日 ×
47 鏈，最大絕對誤差 5.7e-14 < 1e-6）。資料源 `data/chain_daily/series.json`（594KB）走
`ensureChainDaily()` 懶載不進首屏。畫布純 SVG：47 鏈散點、成交額 Top10 錨點標名＋軌跡尾巴、
其餘淡化 hover 可見；日期可回看；候補清單「資金剛輪入」（改善）／「動能領先」（領先）
完整列出。文案全程描述語氣（該專案回測：前瞻超額六種切法 0/12 顯著，資料不支持任何
買賣建議），頁尾附回測依據、成員重疊揭露（一塊錢平均被算進 2.24 條鏈）與「盤中版 →」互連。
新鮮度：最後資料日落後今日 >3 平日顯示「資料未更新」；fetch 失敗／格式異常皆降級不壞整頁。

## 2026-07-28 彙總分析：`max_tokens` 8000 → 16000

前一批的空回應守門只讓「thinking 吃光額度」變成明確失敗＋retry，不降低發生率。實測 5 個時段
共 30 份子分析：**盤後分析 10 份裡有 6 份撞到 8000 上限**（4 份 text 全空、2 份文字被截短），
新聞晨報 1/10，即時類股動態 0/10（最多只用到 2,247 thinking token）——集中在 context 最大的
盤後分析（實測 `input_tokens` 7,774–7,923，新聞晨報 4,344–4,487、即時類股 1,803–3,267）。

Sonnet 5 已移除 `budget_tokens`（送出即 400），**無法單獨限制 thinking**，thinking 與回覆文字
共用同一份 `max_tokens`，所以只剩「拉高上限」或「降 effort」兩條路。選前者：`max_tokens` 是
上限而非預留額度、只按實際生成計費，而一份空白目前是燒 8000 token 換 0 字、加上 retry 等於
16000 換 0 字——**拉高上限比維持現狀更省**。降 effort 則是反方向（官方指引：推理不足應調高
而非調低），且會犧牲最複雜那頁的品質。16000 也是非串流請求的建議上限，不必改成串流架構。

三頁同調（即時類股動態用不到，調高對它零成本）；`effort` 維持 `medium`。三站 `callClaude`
逐字同步。`tests/test_summary_call.py` 新增斷言釘住 16000 與 `thinking.type=adaptive`。
`stop_reason` 已落進 `six[]`，之後若仍見 `max_tokens` 代表 16000 也不夠，屆時再考慮縮 context。

## 2026-07-27 彙總分析：空回應攔截 + 三條 SYS 規則放寬

- **修 bug：AI 空回應未攔截**。adaptive thinking 吃滿 `max_tokens:8000` 時回應只有 thinking block、
  沒有 text block，`callClaude`／`call_claude` 都把它當成功回傳空字串，以 `ok:true` 進彙總
  （2026-07-27 pm 場：6 份中 3 份 `output_tokens=8000`／`thinking≈8000`、`text` 為空，
  `ok_n=6` 通過 `MIN_OK_FOR_SYNTH=3` 檢查，彙總層只好自行宣告「本日 6 份中…為空白」）。
  現在空白（含全空白字元）視為失敗丟出：自動場交給既有 retry，仍空則落 `ok:false` 佔位；
  前端無重試故直接落 `ok:false`。回傳值一併保留 `stop_reason` 並寫進 `six[]` 供事後判讀。
  三站 `callClaude` 逐字同步（postmkt／taiwan-flow-live-v2／taiwan-stock-news）。
- **SYS 規則放寬三條**（原本模型在缺料時自行放寬、與 prompt 明文相牴觸，改為寫成明確規則）：
  `SYS_LIVE (7)` 個股成交量只出現在「個股盤中資金集中 前15」段的「量X張」、該段無資料時整段略過，
  故不硬性套用 1,000 張門檻，查不到量能者可入選但須標注「量能未知」；
  `SYS_NEWS (7)` 美股／晨報資料日與主資料日不同時，由「嚴禁跨日串連」改為可串連但須標注資料日
  （**USER prompt 需一併改**：原 `sumUserNews = sumUserPostmkt` 別名共用同一條「僅可單獨解讀，勿跨日
  比較」，會與放寬後的 SYS 打架；已拆成獨立模板，三份副本措辭一致）；
  `SYS_SYNTH (5)(6)` 彙總層同步鬆綁量能門檻與跨日禁令（新聞晨報資料日不受跨日限制）。
  `SYS_POSTMKT (7)(9)` **不動**——盤後分析頁本身有成交量資料，門檻與日期對齊維持原樣。
- 新增 `tests/test_summary_call.py`（10 支）：空回應／retry 行為，外加 SYS prompt 在
  `index.html` ↔ `build_summary.py` 兩份副本的逐字一致性守門（此路徑原本零測試覆蓋）。

## 2026-07-24 專案優化批次（三輪）

- cache.json（~3MB 增量快取）移出 git 改走 actions/cache；diag/mktbal 資料改懶載（首屏傳輸減半）；
  四 workflow 補 timeout＋失敗告警（開 issue）；日期 tab 落後計算改交易日（排除週末）；`.gitignore` 補齊。
- 抽共用 `src/fmclient.py`（FinMind api_get 統一重試，postmkt 從零重試變有重試）；
  SYS prompt 953 字三份複本去重（唯一事實來源＝`SUM_SYS_POSTMKT`）；requirements 鎖版本。
- postmkt.json 瘦身 2.42MB→1.57MB（lending 衍生欄改由消費端以 px 重建、當沖 by_ratio 停產）；
  抽共用 `src/twseclient.py`（全域節流，postmkt 的 TWSE 端點也納入）；pytest 測試上線（60+ 離線測試）；
  TWT72U 欄位改 fields metadata 動態定位；diag 回補窗常數集中（full/--sample 共用）；
  分點推估 3 併發；`_next_exdiv` 同日現金+股票合併改寫為順序無關；CSP meta 上線；
  commit/push 與失敗告警抽 composite action；日期 tab 快取可刷新；多項顯示小修
  （+0 不上色、stat 列各 tab 用自身資料日、新聞連結 scheme 過濾、診斷 AI 可中斷）。

## 2026-07-21 盤後批次改進四項（依 b-group-investigation 調查結果）

- **項5 ETF 持股加市值欄**：`renderAETF` 持股組合表新增「市值(億)」欄＝`stocks[code][3]/1e8`，
  section 註記「市值依 FinMind 揭露日、非即時」；缺值顯「—」。資料源 build_aetf.py 已補逐股 mv
  （v2 `src/build_aetf.py` `grab_holding()`），但 postmkt 讀 v2 raw latest.json，故要等 v2 排程
  重跑 build_aetf push 後該欄才有實值（在此之前一律「—」，屬預期）。
- **項8 大盤餘額只留金額**：`MKTBAL_PILLS` 由 4 pill（融資/融券/借券賣出/不限用途）縮為 2 pill：
  融資餘額（只 `margin_money` 金額(億)、拿掉張數）＋借券賣出餘額（只 `sbl_short_value` 金額(元)＋
  `mktNum` 千位點、拿掉股數）。融券/不限用途 TWSE/FinMind 官方無金額欄故不顯示；資料檔
  `market_balance_history.json` 欄位不動、僅前端不消費那兩項。
- **項9 融借券整合排行拆 TSE/券商兩區塊**：`index.html` 整合排行表把單一「借券餘額」欄組拆成
  「TSE餘額」「券商餘額」兩區塊各 餘額(張)/異動(張)/市值(億)/市值異動(億)，刪掉合計三欄
  （`plat_total*` 資料保留、摘要仍用不動）。後端 `build_postmkt.py build_lending()` 新增
  `sys_mv_chg`/`otc_mv_chg`（=異動張數×收盤價，同 sbl_short_mv_chg 近似法）寫入 row。
  （註：2026-07-24 瘦身後這批 `*_mv_chg`/`plat_total*` 改由前端 `augmentLending()` 重建，不再落地。）
- **項10 日期 tab 移最右＋文案**：TABS 陣列 `["dates","日期"]` 移到 `["diag","持股診斷"]` 之後；
  「自動產出」section 文案由「早場08:00／晚場22:00」更正為實際 cron「早場06:23／晚場22:47 台北」。
- **驗證**：本機跑 build_postmkt（3481 群創 sys_mv_chg=-376891/otc_mv_chg=109469 千元）＋瀏覽器 11 tab
  零 console error；大盤2pill、融借券兩區塊八欄無合計、ETF市值欄、借券賣出金額帶千位點、日期 tab 在最右皆實測。

## 2026-07-20 主動ETF tab 三項UI改進（純前端，`renderAETF` 內）

- **修「部分ETF點不進去」的bug**：根因是舊版 ETF 總覽表只在 `diff.etfs[code]` 有
  buy/sell（`n_buy`/`n_sell` 非0）時才把 ETF 名稱掛可點（`data-etf`），00981A 等
  當日無主動加減碼的 ETF（`n_buy=n_sell=0`）因此點不進去。改法：`ov` 每列一律
  可點，不再看 `hasDiff`；`state.openEtf` 展開區塊改成先看 `latest.etfs[code]`
  是否存在（持股一定有，只要 latest 載入成功），不再依賴 `diff.etfs[code]` 是否有值。
- **展開區塊重排**：「最新持股組合」（讀 `latest.json etfs[code].stocks`，dict
  `code→[股數,名稱,權重%]`，實測結構）移到「加減碼明細」**上方**，各自標資料日
  （持股＝`src_date`；加減碼＝`de.d0→d1`，該 ETF 若無 diff 條目則退回
  `diff.primary_date`）。無加減碼時顯示「今日無主動加減碼」而非空白兩欄。
- **次產業流向明細補 ETF 名稱**：`so.detail[].etf` 原本只有代號，改用
  `latest.etfs[code].name`（備援 `diff.etfs[code].name`）補上，呈現同
  `code`+`nm` span 樣式（跟個股欄一致）。
- 三項均已本機起 `python -m http.server` 跑 `index.html` 實測（00981A/00403A 兩種
  case＋次產業展開），全 11 個 tab 逐一點擊 console 零 error；未動
  `callClaude`/`mdToHtml`/`linkifyStocks` 等三站同步函式本體。
