# 日期欄語意對照表（五 repo 產出檔）

本文件逐檔實查各產出 JSON 的日期相關欄位，統一記錄「欄位 / 語意 / 時區 / 粒度」，
供跨站資料流除錯與 AI 分析日期對齊參考。建立於 2026-07-14（日期修正大批次 P6）。

## 名詞

- **交易日粒度**：值為某個台股/美股交易日（`YYYY-MM-DD`），與「產出時刻」無關；跨非交易日不推進。
- **時刻粒度**：值為實際產出的瞬間時間（ISO 8601 帶時區）。
- **時區欄位**：本批次（P4）後，所有「產出時刻」欄位統一為台北 `+08:00`；歷史舊檔可能仍是 UTC
  （`...Z` 或 `+00:00`），前端顯示端一律「Date 解析後轉台北」相容新舊（見各站 `fmtGenTaipei`）。

---

## 1. taiwan-flow-live-v2（參考資料庫，資料管線源頭）

### data/morning.json（晨報，每日台北清晨產）
| 欄位 | 語意 | 時區 | 粒度 |
|------|------|------|------|
| `date` | 晨報對應的前一交易日（現貨/籌碼基準日） | — | 交易日 |
| `generated_at` | 晨報產出時刻 | 台北 +08:00（已） | 時刻 |
| `spot.date` | 現貨加權收盤所屬交易日 | — | 交易日 |
| `chips.inst.date` | 三大法人買賣超所屬交易日 | — | 交易日 |

> 註：`date` / `spot.date` / `chips.inst.date` 常等於「產出日的前一交易日」，與 `generated_at` 的日期不同。
> 下游若用 `generated_at` 當籌碼資料日會錯標（本批次 P2 修正的破口，改餵 `chips.inst.date`）。

### data/us.json（隔夜美股，每日台北清晨產）
| 欄位 | 語意 | 時區 | 粒度 |
|------|------|------|------|
| `date` | 美股交易日（隔夜資料本質日） | — | 交易日 |
| `generated_at` | 產出時刻 | P4 由另一 agent 改台北 +08:00（原 UTC） | 時刻 |
| `session` | 盤別文字（盤中/收盤等） | — | — |

> 美股本質為隔夜資料；下游晨報/新聞段的「隔夜美股」資料日一律以 `us.date` 為準（P2 修正）。

### data/aetf/latest.json（主動 ETF 每日快照）
| 欄位 | 語意 | 時區 | 粒度 |
|------|------|------|------|
| `run_date` | 抓取批次的台北日 | 台北 | 交易日 |
| `generated_at` | 產出時刻 | 台北 +08:00（已） | 時刻 |
| `etfs.<code>.src_date` | 各 ETF 投信揭露的持股基準日 | — | 交易日 |

> ⚠ `src_date` 格式不統一：部分投信用斜線 `2026/07/14`、部分用連字號 `2026-07-13`，比較前需正規化
> （`replace("/","-")`）。群益/野村常揭露 T+1（隔一日），本批次 A 案在 v2 側折算回持股基準日。

### data/aetf/diff.json（主動 ETF 加減碼聚合）
| 欄位 | 語意 | 時區 | 粒度 |
|------|------|------|------|
| `generated_dates` | 納入本次聚合的持股基準日清單（斜線格式） | — | 交易日 |

> 常態應為單一持股基準日；混入多日代表有 ETF 揭露落後（A 案「誠實分組」處理，落後檔另列 `laggards`）。

### Worker `/live`（即時快照端點，不落檔；程式在 `worker/src/index.js` 的 `export function aggregate`）
| 欄位 | 語意 | 時區 | 粒度 |
|------|------|------|------|
| `ts` | 全體**有分類個股**中**最後一筆成交**的時戳（掃描時 `001`/`101` 指數列先被 `continue` 跳過，故不含指數）；取 `max(r.date)`。**不是**集合競價收盤時刻，**也不是**本快照的產出時刻 | FinMind 原字串，**無時區標記**（實務上為台北，但產生端從未宣告） | 到微秒（`YYYY-MM-DD HH:MM:SS.ffffff`） |
| `generated_at` | Worker 組出這份回應的牆鐘時刻 | **UTC `...Z`**（`new Date().toISOString()`，**非**台北 +08:00） | 時刻 |

> 註 1（**收盤後會被推進**）：`ts` 取 max，收盤後會被**盤後定盤／零股**成交推進到 **14:30–15:00**，
> 而非正規盤 13:30 集合競價（2026-07-21 由「第一檔 date」改 `max(date)` 時的已知行為，
> 當時線上實測 ts 由 `13:12:51` 變成 `15:00:00`）。
> 註 2（**非交易日為盤前殘留**）：非交易日 `/live` 的 `ts` 可能是**當日盤前的殘留時戳**，
> 其**日期部分與內容的實際資料日不符**——2026-08-09（休市日）線上實打得
> `ts="2026-08-09 08:30:00.000000"`，內容卻是 08-07 的收盤值。
> 故 **`ts` 的日期部分不可當資料日的權威來源**，只能當「上游最後成交時戳」讀。
> 註 3（**與 `generated_at` 的差別**）：同一份 payload 裡兩者語意與時區都不同——
> `ts` 是**上游**最後成交、無時區標記；`generated_at` 是**我方 Worker** 產出、UTC `Z`。
> 前端並列顯示時務必分別標明（live-v2 `#tsline`、本站「日期」tab）。
> 註 4（**改語意屬待裁決的 C 案，目前未做**）：若要讓 `ts` 語義＝正規盤收盤，須限定只取
> ≤13:30 的 rows 或改用指數列 `001`/`101` 的 date——那是**改 `/live` 回傳值**，會連鎖到
> `computeFlow` 窗計算、`framesDegenerate`、`flowLastPayload.date`、daysummary 產物 `date`、
> 本站日期 tab 落後判定、AI 分析主資料日與已歸檔的 intraday 回測樣本語意，使用者尚未裁決。

### 其他
- `data/classify.json`：分類對照表，無交易日語意。

---

## 2. taiwan-flows（盤後法人動態）

### data/latest.json
| 欄位 | 語意 | 時區 | 粒度 |
|------|------|------|------|
| `date` | 盤後法人主資料交易日 | 台北 | 交易日 |
| `generated_at` | 產出時刻 | 台北 +08:00（已） | 時刻 |
| `pages.foreign.futures_card.date` | 台指期未平倉資料日 | — | 交易日 |
| `pages.foreign.futures_card.prev_month_end` | 對比基準：上月最後交易日 | — | 交易日 |

### data/meta.json
| 欄位 | 語意 | 時區 | 粒度 |
|------|------|------|------|
| `generated_at` | 產出時刻 | 台北 +08:00（已） | 時刻 |
| `baseline_date` | 投信庫存累計種子日（凍結於 2026-04-30，非市值基準） | — | 事件日 |
| `stocks.<code>.issued_lots` | 發行張數（市值換算基準）；**每交易日**依集保 `NumberOfSharesIssued` 更新 | — | 交易日 |

> 易誤解：`baseline_date` 不是「發行股數基準日」，而是投信庫存累計的起算種子日（凍結），
> 動它會破壞庫存累加。市值用的發行張數 `issued_lots` 本身每交易日更新，並非凍結在 baseline。

---

## 3. taiwan-stock-news（新聞晨報）

### news.json
| 欄位 | 語意 | 時區 | 粒度 |
|------|------|------|------|
| `generated_at` | 產出時刻（每日三班：06:30/15:00/22:37 台北都會更新） | P4 改台北 +08:00（原 UTC `...Z`） | 時刻 |
| `trading_days` | 納入本檔的交易日清單（末筆＝主資料日 primary） | — | 交易日 |
| `stocks[].news[].date` | 單則新聞發布日 | — | 日（發布日） |

> 前端主資料日 `primary` = `trading_days` 末筆（無則退 `day10(generated_at)`）。
> 下游閘門用 `generated_at` 的台北日 + 時刻判斷班次（早班 >=06:00、晚班 >=21:00），故時區必須正確。

---

## 4. postmkt（盤後分析＋彙總分析）

### data/postmkt.json
| 欄位 | 語意 | 時區 | 粒度 |
|------|------|------|------|
| `date` | 盤後主資料交易日（各段 date 取 max） | 台北 | 交易日 |
| `generated_at` | 產出時刻 | P4 改台北 +08:00（原 UTC `+00:00`） | 時刻 |
| `margin/lending/short_balance/daytrading/blocktrade.date` | 各 dataset 的交易日 | — | 交易日 |
| `oddlot.intraday.date` / `oddlot.after.date` | 零股盤中／盤後交易日 | — | 交易日 |
| `brokers.date` | 分點查詢預設日（與當沖對齊） | — | 交易日 |
| `date_mismatch` | P5 新增：借券 tab 落後偵測 `[{name,date}]`，非空＝有 dataset 落後於 `lending.date` 基準 | — | 交易日 |

> `lending.date` 是借券 tab 多 dataset 的對齊基準（取短餘額表日）；某 dataset 與之不同即進 `date_mismatch`。

### data/summary/YYYYMMDD-{am|pm}.json（自動彙總產出）
| 欄位 | 語意 | 時區 | 粒度 |
|------|------|------|------|
| `generated_at` | 產出時刻 | 台北 +08:00（`taipei_now`） | 時刻 |
| `date` | 產出的台北日（檔名日） | 台北 | 日 |
| `dates` | P3 新增：三頁各自主資料日 `{頁面名: primary}` | — | 交易日 |
| `six[].date` | 各份摘要對應頁面的主資料日 | — | 交易日 |
| `slot` | 場次 `am`/`pm` | — | — |

> `date`/`generated_at`（產出日）與 `dates`（三頁資料日）語意不同：前者是何時跑，後者是分析的是哪一交易日的資料。

### data/analyses/*.json（前端手動分析雲端存檔）
| 欄位 | 語意 | 時區 | 粒度 |
|------|------|------|------|
| `at` | 產出時間字串（`twNow`） | 台北 | 時刻 |
| `date` | 分析主資料日 | — | 交易日 |

---

## 5. shihpc.github.io（總入口）

僅 `index.html` 靜態入口頁，無產出資料檔、無日期欄位。

---

## 跨站對齊要點速查

1. **產出時刻一律台北 +08:00**（P4 後）；讀舊檔用「Date 解析轉台北」相容。
   **例外**：Worker `/live` 的 `generated_at` 仍是 UTC `...Z`（即時端點、不落檔，未納入 P4 批次），
   同 payload 的 `ts` 更是**完全無時區標記**的上游原字串——兩者都靠「Date 解析轉台北」是不夠的，
   `ts` 根本不可被 `Date.parse` 當帶時區的值處理，詳見上方 Worker `/live` 段。
2. **資料日 ≠ 產出日**：晨報 `chips.inst.date`、美股 `us.date` 都可能落後產出日一個交易日，
   AI context 的 dlabel 一律餵「資料日欄」而非 `generated_at`（P2）。
3. **格式正規化**：跨站比較日期前先 `replace("/","-")`（aetf `src_date`、`generated_dates` 用斜線）。
4. **落後偵測**：postmkt `date_mismatch`（借券多 dataset）、aetf `laggards`（ETF 揭露落後）各自成段，前端顯示徽章/警示。
