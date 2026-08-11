# postmkt — 盤後分析

台股盤後資料的靜態儀表板（單一 `index.html`，無 build 工具），
是[股市雷達 Hub](https://shihpc.github.io/) 的子站之一。

## 十二個 Tab（2026-08-11 加「輪動雷達」後）

| Tab | 資料源 | 內容 |
|---|---|---|
| 摘要分析 | 前端彙整以下各 tab ＋ 即時呼叫 Anthropic Claude API | AI 生成以查找 alpha 標的為目標的洞見（首頁預設 tab） |
| 彙總分析 | 三頁面（本站盤後/即時類股/新聞晨報）context × Sonnet5 各 2 次 ＝ 6 份摘要 → Opus4.8 彙總 | 跨份共振精粹 alpha：方向預測＋進出建議；手動一鍵（近2次存瀏覽器）＋自動場（近3日、讀 `data/summary/`） |
| 主動ETF | taiwan-flow-live-v2 `data/aetf/`（跨 repo 唯讀，資料源 FinMind、20+ 檔） | 每日投組快照、主動加減碼**兩欄並列**（主動純額 net_active｜含申贖 raw_change）、進出個股、次產業流向（含ETF名稱）；點 ETF 一律可展開，先看**最新持股組合**（標持股基準日）再看加減碼明細（標資料日，無加減碼顯示「今日無主動加減碼」）；彙整含跨ETF共識與「主動 vs 含申贖」解讀 |
| 融資券借券 | FinMind 融資/融券/借券 + TWSE TWT72U 兩平台借券餘額 | 個股查詢（點開完整明細）＋整合排行（全市場 2200+ 檔、分組雙列表頭、虛擬捲動） |
| 當沖 | FinMind `TaiwanStockDayTrading` + `TaiwanStockPrice` + `TradingDailyReport` | 當沖排行（含漲跌幅/振幅/分點推估） |
| 鉅額交易 | FinMind `TaiwanStockBlockTrade`(+`BlockTradingDailyReport`) | 當日逐筆列表，同股分組、買賣方分點盡力比對 |
| 零股 | TWSE `TWTC7U`（盤中）/`TWT53U`（盤後），公開端點免金鑰 | 盤中/盤後兩子標籤，個股成交股數/筆數/金額 |
| 分點 | FinMind `TaiwanSecuritiesTraderInfo`＋`TradingDailyReport` 專屬 endpoint | 單點（查分點進出個股）/個股（查個股進出分點）/清單（1010 分點模糊查找） |
| 大盤餘額 | FinMind `TaiwanStockTotalMarginPurchaseShortSale`（融資/融券）＋ TWSE `TWT72U`（借券賣出，SLB+NLB整體市場相加）＋ TWSE `TWTA1U`（不限用途借貸，6 selectType加總） | 全市場層級四項餘額合計（融資/融券/借券賣出/不限用途款項借貸），4 pill 切換，近5日逐日＋近3年各月底（年列可展開），與「融借券」tab（個股排行）明確區分定位 |
| 輪動雷達 | taiwan-flow-live-v2 `data/chain_daily/series.json`（跨 repo 唯讀，594KB 懶載，283 交易日 × 47 條產業鏈日頻序列，每交易日夜間增量更新） | 盤後日頻 RRG（B-ew 軸：等權報酬相對大盤等權基準，n=12 z-score／k=10 動能）：47 鏈散點＋成交額 Top10 錨點軌跡尾巴、日期回看、候補清單（改善／領先象限，持續性 N=3 完整列出）、新鮮度提示；描述語氣、附成員重疊揭露與盤中版互連 |
| 日期 | 即時 fetch 八個資料源的 date/generated_at | 全專案資料日期總覽：各源資料日/產出時間(台北,到分)/新鮮度狀態（最新/落後N個交易日，僅排除週末、國定假日不扣），一眼看清哪些資料到今天 |
| 持股診斷 | `data/diag/diag.json`（`src/build_diag.py` 夜間管線）＋ v2 `/live` 現價＋ taiwan-stock-news 新聞 | 輸入持股（僅存 localStorage）→ 逐檔五面向（籌碼/價量/題材/基本面/系統）紅黃綠燈號＋事實清單＋組合層檢查＋近3日新聞命中＋可選 AI 解讀 |

原「融資」「融券借券賣出餘額」兩 tab 於 2026-07-11 併入「融資券借券」整合排行（該 tab 為個股層級排行）；
2026-07-19 新增「大盤餘額」tab 補上大盤層級（全市場合計）視角，兩者並存、口徑用途不同。

## 架構

- `src/fmclient.py`：四支 Python 管線共用的 FinMind client（api_get 統一重試：
  402/429 限流等 65 秒、其他錯誤等 3 秒）＋ `token()`／台北時區工具（2026-07-24
  抽出，原本四份實作重試策略互異、build_postmkt 甚至零重試）。
- `src/twseclient.py`：TWSE 公開端點共用 client——全域節流鎖（IP 限流教訓，見「已知教訓」），
  build_postmkt 與 build_mktbal 共用。
- `tests/`＋`.github/workflows/test.yml`：離線單元測試（pytest，免 token/網路），
  覆蓋日期閘門（news_fresh 跨午夜、slot_trading_day 延遲跨日、is_twse_holiday 民國年/
  fail-open——都是實際踩過的坑）、diag 純函式（streak/rev_metrics/_pctile/_roc_date）、
  fmclient 重試語意、零股合計列過濾、lending 瘦身欄位形狀與重建公式對齊。
  本機跑：`python -m pytest tests/ -q`。Python/測試檔變動時 CI 自動跑。
- `build_postmkt.py`：抓 FinMind dataset＋TWSE 公開端點的最新交易日全市場資料，
  各 tab 預先聚合排序，輸出 `data/postmkt.json`（~1.6MB；2026-07-24 瘦身：lending.rows
  只落地基礎量＋px 收盤價，市值/金額衍生欄由前端 `augmentLending()` 與
  `build_summary.py _augment_lending()` 載入後重建，公式三處需一致；當沖 `by_ratio`
  比重榜同時停產——前端從未渲染，停產順帶把分點推估查詢從 ~100 檔減半）。
  TWSE 端點（TWT72U/零股）改走 `src/twseclient.py` 全域節流。
  找不到最新交易日資料時自動往前回退最多 5 天；TWSE 端點失敗重試一次後降級（缺欄警示）。
- `.github/workflows/build.yml`：平日 21:53 台北（13:53 UTC）排程＋手動觸發
  （2026-07-14 起由 21:30 延後：FinMind 當沖量值約 21:30 後才更新，留緩衝＋
  冷門分鐘避開壅塞），跑完自動 commit `data/postmkt.json`。
- 預產資料的 tab 前端不直連 FinMind（token 走 Actions secret）。
- **例外：分點 tab 的「單點/個股」是互動查詢**（無法預產 1010 分點×2215 檔組合），
  前端直呼 FinMind `/api/v4/taiwan_stock_trading_daily_report`（CORS 開放）。
  token 由使用者在頁面輸入一次、只存瀏覽器 localStorage，不進 repo。
- **例外：摘要分析 tab 前端即時呼叫 Anthropic Claude API**（`insightHtml`/`runInsight`
  /`callClaude`）。`insightGatherContext()` 把主動ETF/融借券/當沖/鉅額/零股盤中彙整成
  ~2.4K token 精簡文字，`insightFetchBrokers()` 即時抓 4 個指定分點（9268/9800/9600/9A00），
  組成 prompt 送 Claude（`anthropic-dangerous-direct-browser-access:true` header 開瀏覽器
  CORS，已實測）。Anthropic token 存 localStorage `anthropic_key`，只送 Anthropic，不進 repo。
  模型 `state.insightModel`（預設 `claude-opus-4-8`）。輸出走 `mdToHtml()` 極簡 markdown 渲染。
- `src/build_mktbal.py`：大盤層級四項餘額（融資/融券/借券賣出/不限用途借貸）管線，輸出
  `data/market_balance_history.json`（daily 近30交易日＋monthly 近36月底，皆升序陣列）。
  `.github/workflows/mktbal.yml`：平日 22:20 台北排程（排在 build.yml/diag.yml 之後）＋
  push-paths 首推＋workflow_dispatch（可帶 backfill）。`--backfill` 一次性回補 3 年，
  TWSE 抓取全域節流(預設4秒/請求，`TWSE_THROTTLE`可調、舊名`MKTBAL_TWSE_THROTTLE`仍相容；
  節流鎖 2026-07-24 抽到 `src/twseclient.py` 供 build_postmkt 共用)＋指數退避重試(2/5/10/20秒)，
  避免連續打 TWT72U/TWTA1U 觸發 IP 限流（2026-07-19 修：前一版無節流，backfill 連抓
  約6次後被限流回空、近八成月份 sbl/unrestricted 全 null；修完 64 個回補日期全數 0 null）。
- 主動ETF tab 直接讀 taiwan-flow-live-v2 的 raw JSON，不搬遷該站管線。
- **日期欄語意**：五 repo 所有產出檔的日期欄（欄位/語意/時區/粒度）對照表見
  [`docs/date-semantics.md`](docs/date-semantics.md)——跨站資料流除錯或調整 dlabel 對齊時先讀它。

## 快速接手

帶日期的歷次變更紀錄已搬到 [`CHANGELOG.md`](CHANGELOG.md)（2026-07-24 起）；
本節只留接手需要的常青內容（各 tab 口徑、資料流、教訓、維護約定）。

### 輪動雷達 tab（2026-08-11 上線）

- **口徑（第二階段定案，不得單方更改）**：B-ew＝價格版 RRG × 等權報酬。X 軸 RS-Ratio＝
  鏈等權報酬指數 ÷ 大盤等權報酬指數（RS）再做 12 日窗 z-score（100＋標準差倍數）；
  Y 軸 RS-Momentum＝RS-Ratio 的 10 日 ROC 再做同法 z-score。象限 100/100 分界、不做中位置中，
  右上起順時針＝領先／轉弱／落後／改善。候補清單（改善＝「資金剛輪入」、領先＝「動能領先」）
  套持續性 N=3（連續 3 日在該象限才列入），完整列出不設上限，排序 RS-Momentum 遞減。
- **公式正本**：taiwan-flow-live-v2 `backtest/run_rrg_daily_axes.py` 的 `axis_systems`
  （`price_coords` 分支）。前端 `index.html` 的 `RRGD-PURE` 註解區塊逐式重現（含 `cum_index`
  開頭缺值當 0／中間缺值截斷、樣本標準差 ddof=1、SD=0 回 null 等細節），改公式必須兩邊同步
  並重跑對拍（抽 3 日期 × 47 鏈，(x,y) 誤差 <1e-6；2026-08-11 實測最大誤差 5.7e-14）。
- **文案紀律**：該專案回測結論為前瞻超額六種切法 0/12 顯著——資料不支持任何買賣建議，
  全 tab 描述語氣；頁尾固定附回測依據句、成員重疊揭露（一塊錢平均被算進 2.24 條鏈；
  14 條鏈成交額 100% 來自同時屬於其他鏈的成員）與「盤中版 →」互連（兩者軸定義不同）。
- **前端**：插入式改動，命名全用 `rrgd*` 前綴——`TABS`／`SUBS`／`state`（rrgd/rrgdErr/
  rrgdLoading/rrgdIdx）／`render()` 分派／`renderStats()` 各一處；`ensureChainDaily()` 懶載
  （比照 `ensureMktbal()`，594KB 不進首屏），載入後 `rrgdPrep()` 驗格式＋算好座標放模組層
  `RRGD` 快取，格式異常 throw 走 `rrgdErr` 降級文案（fetch 失敗同路徑），不壞其他 tab。
  畫布為純 SVG 字串（`rrgdSvg()`，零外部依賴），880px 單欄圖上清單下。日期選擇：
  `#rrgdSel` 下拉（change 委派）＋ `data-rrgdstep` 前後鍵（click 委派），只列可算出座標的
  日期（`valid_idx` 同式：當日至少半數鏈有座標）。新鮮度：`rrgdLagDays()` 以平日粗略計，
  落後今日 >3 平日顯示「資料未更新」（上游＝v2 baseline 班掛的增量更新，斷了這裡看得出來）。
- **未解／待觀察**：錨點標名未做碰撞避讓，點密集日可能重疊；新鮮度以平日近似、
  國定假日連假可能誤報 1-2 日（與「日期」tab 的 `dayDiff` 同一已知近似）。

### 大盤餘額 tab（2026-07-19 上線）

- **口徑**：融資餘額/融券餘額直接取 FinMind 該 dataset 的 TodayBalance；借券賣出餘額＝
  TWSE TWT72U 的 SLB＋NLB「整體市場」合計列相加（兩個獨立借券管道、彼此不重疊）；
  不限用途款項借貸餘額＝TWTA1U 六個 selectType（X/A/F/G/B/I）「證券商不限用途款項
  借貸／今日餘額」欄逐列加總，欄位位置從回傳 `groups` metadata 動態定位（6類別欄位配置
  不同，不可假設固定 index）。
- **抽核紀錄（2026-07-17，獨立於程式碼之外直接打即時 API 核對）**：FinMind
  margin_shares=9,348,875／margin_money=587,962,513,000／short_shares=236,300 全一致；
  TWT72U SLB 16,910,017,000股/2,718,883,468,980元＋NLB 13,331,897,000股/742,675,370,680元
  ＝30,241,914,000股/3,461,558,839,660元，與 sbl_short_shares/sbl_short_value 一致；
  TWTA1U 六類別加總 16,092,652（仟股），與 unrestricted_shares 一致。
- **已知教訓**：backfill 對 TWSE 端點是逐日高頻查詢（每日2+6次子請求 ×64個回補日），
  無節流會在約6次請求後被 TWSE IP 限流（回應變非JSON空內容），且限流後不會自動解除，
  導致近八成資料全 null——务必保留 `twseclient.throttled_get()` 的全域節流鎖與退避重試，
  不要為了「加速」拿掉。
- **前端**：`index.html` 插入式改動——`MKTBAL_PILLS`/`renderMktbal()`/`mktThead`/
  `mktCell`/`mktDiffCell` 等函式與 `renderDates()` 同樣繞過 `renderPM()`，直接掛在
  `render()` 分派（`state.tab==="mktbal"`）。年展開狀態存 `state.mktbalOpen`，
  比照 taiwan-flows 的 `ffOpen`/`ffToggle` 機制（點擊委派在 `document` 的
  `data-mktyr`/`data-mktsel`）。
- **未解／待觀察**：前端月度年列的 YoY 比較只用「該年最後一筆 vs 去年最後一筆」，
  非嚴謹交易日對齊；`mktbal.yml` 上線後首次排程觸發尚未實跑驗證（本次僅驗證
  push-paths 首推），需留意下個交易日 22:20 後產物是否正常更新。

### 持股診斷 tab（2026-07-18 兩子期上線）

- **資料流**：`src/build_diag.py`（`.github/workflows/diag.yml`，平日台北 22:10、排在
  build.yml 21:53 後）→ `data/diag/diag.json`（全市場日均成交值前 1200 檔，<2.5MB）＋
  `data/diag/cache.json`（增量快取；2026-07-24 起不進 git、改由 diag.yml 以
  actions/cache 跨 run 保存——快取被淘汰時管線自動全量重建，只是該晚 API 呼叫較多）。
  來源：FinMind 價量/法人/融資/借券/千張大戶/月營收/
  PER/股利公告（token 走 Actions secret；千張大戶與 PER3年百分位/除權息只能單檔查，
  採每晚上限輪替刷新 `HOLD_CAP`/`VAL_CAP`）＋ TWSE/TPEx 處置注意 OpenAPI（免金鑰；
  TPEx 站憑證缺 SKI 需 `verify=False`）＋ v2 raw（classify/morning/us）＋本站 postmkt.json
  （券資比/當沖量 merge）。前端另即時抓 v2 `/live`（現價）與 taiwan-stock-news `news.json`
  （新聞命中，本機過濾）。本地驗證：`python src/build_diag.py --sample`（免 token）。
- **燈號規則位置**：全部集中在 `index.html` 的 `DIAG_RULES` 陣列（資料驅動條件表，
  單一定義處；聚合邏輯在 `diagLights()`）。誠實性約定：`ver`/`tag` 標「已驗證（附回測出處）
  ／描述性／交易所公告」；`addon:true`（土洋同買）為疊加條件，需另有**非系統面**綠燈
  才計入，不單獨亮綠。權重為先驗設定，待回測校準。
- **隱私設計**：持股清單只存 localStorage `pm_holdings`，不進任何網路請求 payload、
  不走 gh_token 雲端。tab 內對外請求全是固定 URL 唯讀 GET（diag.json／live／news.json）。
  AI 解讀按了才呼叫 Claude，送出＝該股事實＋市場＋組合層「彙總指標」，
  **不含持股清單/股數/成本**（UI 有明示）。
- **待觀察／待辦**：TPEx 上櫃「注意股」無公開 OpenAPI 端點（2026-07 swagger 查證），
  `at` 欄僅涵蓋 TWSE，列待辦；千張大戶/估值百分位靠輪替刷新，首週資料逐晚補齊；
  燈號規則未經整體回測（僅個別訊號有站內回測出處），校準後調 `DIAG_RULES` 即可；
  盤中行為（/live 降級、即時損益）未在開盤時段實測。

- 前端表格框架 `tbl(cols, rows, opts)`：表頭排序（`col.sortVal` 供複合欄位給原始值）、
  分組雙列表頭（`col.g`）、加總列（`opts.totals`，sticky 在表頭下）、凍結首二欄
  （`opts.s2`）、>200 列自動虛擬捲動。sticky 相關已知坑全記在 `<style>` 區註解：
  border-collapse/`.tblbox` padding 與 overflow 裁切邊界差（sticky top/left 要設負 padding 值）、
  thead 兩列要 `<tr>` 本身 sticky、rAF 在背景分頁不觸發（量測用 setTimeout、
  虛擬捲動有 300ms 輪詢保險，背景分頁跳過）。
- TWSE 端點的「合計」市場總計列要濾掉（代號欄非 ASCII 英數），TWT72U/TWTC7U/TWT53U 都有。
- 分點查詢聚合：張數保留小數、只在顯示時捨入（先逐列 round 再加總會偏差且讓個股
  買賣超合計出現假非零）。金額顯示單位＝百萬元 1 位小數（`milF/milS`，÷1e6，僅此表用；
  2026-07-16 由原「萬元」改；排序仍用原始 `b_amt/s_amt` 未除）。
- 摘要分析：LLM 洞見機制由使用者決定用「LLM 前端即時」（見 2026-07-11 對話）；
  system prompt 強制「只描述歷史統計傾向、非投資建議、非預測、每個觀察可追溯數據」，
  符合工作區共同原則。本站是四站摘要分析的**範本站**，套用已完成（2026-07-12）：
  即時類股動態（taiwan-flow-live-v2）與新聞晨報（taiwan-stock-news）已各自新增同框架 tab；
  盤後法人動態（taiwan-flows）**不加 tab、該站零改動**，其法人資料改併入本站 insight——
  `TF_BASE` 常數（`raw.githubusercontent.com/shihpc/taiwan-flows/main/data`）、`load()`
  平行抓 `latest.json` 存 `state.tf`（失敗不擋），`insightGatherContext()` 新增
  「三大法人買賣超」段（外資買超前10/賣超前6＋台指期未平倉、投信買超前10/賣超前6，
  dlabel 跨日警告自動生效）。SYS prompt 未改。退版點：git tag `pre-insight-tab`。
- 彙總分析 tab（2026-07-12 新增，第 8 個 tab）：一鍵 6+1 呼叫（3 頁 context×每頁 2 次
  ＋Opus 彙總，約 NT$8-10/次）。**2026-07-12 起 6 份摘要全改 Sonnet 5（每頁×2 次獨立分析、
  標籤 Sonnet5-A/B 去重）、彙總維持 Opus 4.8，成本考量。**彙總 SYS 以「跨份共振優先」
  （N/6 份提及）精粹 alpha、給方向預測與進出建議。單份失敗不中止（≥3 份成功才彙總）。手動近 2 次存 localStorage
  `summary_manual`；自動場由 `build_summary.py`＋`summary.yml`（cron 06:23/22:47 台北觸發——提早＋錯開整點
  避開 GitHub cron 壅塞（UTC 00:00 整點延遲常達 2-3 小時），由資料齊全輪詢閘門等資料
  （2026-07-14 依審計改造：pm 硬等 postmkt/news晚班(>=21:00)/taiwan-flows 三源皆今日、最多 170 分，
  逾時=假日 skip；am 先硬等 morning.json 最多 150 分，通過後軟等 us.json＋news早班(>=06:00)
  最多 60 分、逾時照跑），輸出 `data/summary/YYYYMMDD-{am|pm}.json` 保留近 3 日，前端列表點閱。
  **假日/颱風假**（2026-07-12 補強）：閘門進場先查 TWSE 休市行事曆 API（免金鑰）擋排定
  假日——am 場必須靠這層（晨報管線假日仍會更新 generated_at，資料閘門擋不住）；行事曆
  混有「開始交易日」等交易日標記，過濾規則見 `is_twse_holiday()` 註解；API 失敗
  fail-open 續走資料閘門。颱風假等臨時停市無盤前可查來源：pm 場由 postmkt.json `date`
  回退機制天然防住；am 場會誤跑一次（約 NT$9、每年 2-4 次），屬已評估接受的殘餘風險。
  **維護重點**：三站 gather 邏輯在本 repo 有兩份移植副本（index.html 的 sumCtx* 與
  build_summary.py 的 gather_*），三站前端 insightGatherContext/SYS 改動時需同步兩處
  （SYS 已驗逐字一致）。自動場需 repo Secret `ANTHROPIC_API_KEY`（見部署設定）。
  **2026-07-16 修（pm 晚場永久缺存檔根因）**：pm 閘門的 `news_fresh` 原硬性要求新聞
  `generated_at` 為「同日且台北 ≥21:00」，但新聞晚班常因觸發延遲跨過台北午夜才落地
  （generated_at 滾成隔日 00:1x、hour=0），兩條件同破且跨日後永久失敗 → pm 場天天 skip、
  `data/summary/*-pm.json` 從未產出。修法＝`news_fresh` 加 `next_day_before` 參數，pm 呼叫
  傳 `5`：額外接受「隔日 00:00~05:00 前」的晚班（該時窗無別班次，可安全視為前一交易日晚班）；
  am 場未帶新參數、行為不變。**跨 repo 依賴**：真正讓新聞跨午夜的是 `taiwan-stock-news`
  的備援 schedule（原台北 22:37，被 GitHub cron 延遲到 00:17 覆蓋掉 Worker 22:07 已寫好的
  22:18 好資料）——已同步把該備援前挪到 21:37（`build-news.yml`，2026-07-16），使延遲也不跨
  午夜、不覆蓋。新聞晚班主觸發是 taiwan-flow-live-v2 Worker 每小時 :07 的 workflow_dispatch
  （準點、最後一班台北 22:07），schedule 僅備援。兩處互補：Worker 保正常、閘門放寬當最終防線。
  注意此修只影響未來場次，歷史缺的 pm 場不回填（要補需手動 workflow_dispatch）。
- 個股外連＋雲端儲存（2026-07-12）：三站（本站/taiwan-flow-live-v2/taiwan-stock-news）insight
  渲染＋本站彙總渲染中，個股代號自動變連結外開 Yahoo 技術分析頁。`linkifyStocks(html, knownSet)`
  雙層防誤連：各站 `stockCodeSet()` 收集已知代號＋型態兜底（代號緊跟中文、單位黑名單、
  「元大/元太」例外），tag 切分不破壞 HTML。分析結果自動存本 repo `data/analyses/`
  （`insight-{postmkt|live|news}-YYYYMMDD.json`／`summary-manual-YYYYMMDD.json`，當日陣列、
  單日上限10筆，保留近3日由 build_summary.py 清理段順手刪）。寫入靠 localStorage `gh_token`
  （三站同 origin 共用，未設靜默跳過）；讀取免 token——彙總 tab「雲端歷史（近3日）」列 4 種檔、
  v2/news 各列自站，raw CDN 約 5 分快取。**維護點**：`linkifyStocks`/`ghSaveAnalysis` 三站逐字一致，改動需三站同步。
- 待辦（暫緩）：回測模組（nightly pipeline 累積歷史→前向報酬勝率餵 prompt），
  使用者 2026-07-11 決定暫緩，規格未定義。
- 未解：分點互動查詢在無 token 環境只能看到輸入提示；FinMind 個股層級維持率、
  投信/自營商持股水位等官方未公開，明細面板已註明不提供。
- 未解：摘要分析在無 Anthropic token 環境只能看到輸入提示（無法自動化驗證真實 200 回應，
  已用 dummy key 驗證 CORS/請求格式/錯誤處理/回應解析路徑）。

## 部署設定（需手動做一次）

1. **Secret**：repo Settings → Secrets and variables → Actions →
   New repository secret，名稱 `FINMIND_TOKEN`，值填 FinMind API token（Sponsor 方案）。
   另加 `ANTHROPIC_API_KEY`（Anthropic API key）供彙總分析自動場（`summary.yml`）使用；
   未設時自動場會失敗、前端顯示「尚無自動產出」，手動一鍵不受影響。
   費用參考：自動雙場 × 約 22 交易日 ≈ NT$350-450/月（摘要用 Sonnet 5、彙總用 Opus 4.8；
   Sonnet 5 介紹價 input $2/output $10 per MTok 至 2026-08-31，之後恢復 $3/$15、月費略升），
   計入該 key 的 Anthropic 帳戶。
2. **GitHub Pages**：Settings → Pages → Source 選 `Deploy from a branch`，
   Branch 選 `main` / `(root)` → Save。
3. （可選）Actions tab 手動跑一次 `build postmkt data` 產生第一份資料。
4. （可選）GitHub Fine-grained PAT：Settings → Developer settings → Fine-grained tokens，
   只勾本 repo、權限 Contents Read/Write，貼進頁面 `gh_token` 欄（三站設一次即可），
   供分析結果雲端儲存；不設不影響其他功能。

## 本機開發

```bash
pip install -r requirements.txt
FINMIND_TOKEN=xxx python build_postmkt.py
python -m http.server 8000   # 開 http://localhost:8000
```
