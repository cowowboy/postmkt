# Changelog

帶日期的變更紀錄從 README「快速接手」搬出集中於此（2026-07-24 起）；
更早的逐日歷史見 git log。常青的架構／口徑／教訓說明仍在 README。

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
