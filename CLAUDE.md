# CLAUDE.md — postmkt 接手速覽

<!-- CANON:BEGIN v1 -->
<!-- 唯一事實來源＝shihpc/claude-harness 的 CANON.md。以下區塊在五個 repo 的 CLAUDE.md 頂端
     有 byte-identical 逐字副本，由各 repo 的 .github/workflows/canon.yml 守門（比對 sha256）。
     改動流程：先改 claude-harness/CANON.md → 跑 tools/sync_canon.py 同步五份 → 更新守門 hash。
     不要只改單一 repo，CI 會擋下來。 -->

## 通用工作鐵律（五個 repo 逐字相同，勿單獨修改）

1. **機密**：token／金鑰一律走 `.env` 或 Actions secret，絕不寫進任何會 commit 的檔案、log 或
   對話輸出。commit 前用 `git diff --staged` 檢查有無夾帶金鑰樣式字串（`sk-ant-`、`ghp_`、`eyJ` 開頭）。
2. **指揮官不下場**：掃 repo、通讀 >300 行的檔、一次讀 >3 個檔、查網頁研究、批次改檔、
   驗收改過的東西——這六類一律派 subagent，主對話只收結論＋`檔案:行號`。
   雲端 session 的 subagent 派工（含第 3 條驗收）已獲常備授權，需要時直接派，不需逐次詢問。
3. **先寫驗收條件再動手**：動手前先寫下目標專案完整路徑＋怎樣算完成＋怎麼驗。改完派
   fresh-context subagent 驗收——**改東西的 agent（含主對話自己）不得擔任驗收者**。
4. **不確定不亂說**：陳述事實（尤其技術細節、數字、外部服務的限制與行為）要嘛附佐證（官方
   文件、實測、`檔案:行號`），要嘛明說「這點我不確定，需要查證」，不可憑印象當確定講。
   區分「已驗證事實」與「推測」，推測要標明。
5. **一次只做一件事**：只做明確要求的那件事，做完給簡短結果；少主動丟一堆延伸提案。
6. **完成的定義**：驗收條件逐條打勾＋fresh-context subagent 驗過＋產物在使用者拿得到的位置。
   **沒實跑過不算完成**。涉及部署者另需 push＋部署 workflow 成功＋**線上驗證本次變更的具體內容**
   （破快取 raw URL／curl／瀏覽器實查），只寫在本機不算完成。
7. **push 前**：先 `git fetch`；`git log --oneline main..origin/main` 非空必須先看內容（訊息／
   時間戳／diff）。一般 push → rebase 整合，嚴禁直接覆蓋；force push 前若 origin 領先的 commit
   是真實新工作 → 停下來問，授權「這次 force push」不等於授權蓋掉 origin 所有領先 commit。
8. **新指標／訊號先問有沒有回測依據**，沒有就先驗證再上線。市場內容可做情境判讀與多空
   因素分析，但須附依據、區分事實與推論；可研判市場與大眾情緒對該數值或新聞的可能反應。
9. **語言**：對話與文件用繁體中文；程式碼註解可中文，identifier 用英文。

> 判準細則、派工模板、教訓簿見 `shihpc/claude-harness`（private）。雲端 session 需 add_repo 才讀得到。
<!-- CANON:END v1 -->

台股盤後靜態儀表板：**單一 `index.html`（無 build 工具，這是刻意的專案定位，勿拆檔）**
＋ 4 支 Python 管線 ＋ GitHub Actions 排程產資料進 `data/`，GitHub Pages 從 main root 服務。
詳細架構與各 tab 口徑見 README.md；歷次變更見 CHANGELOG.md；日期欄語意見 docs/date-semantics.md。

## 佈局

- `index.html`：11 個 tab 全部前端（CSS/JS 內嵌）。`render()` 分派各 tab；共用表格框架 `tbl()`
  （排序/分組表頭/凍結欄/虛擬捲動，sticky 的坑記在 `<style>` 註解）。
- `build_postmkt.py` → `data/postmkt.json`（主資料，五個盤後 tab）
- `build_summary.py` → `data/summary/`（AI 彙總自動場；含資料齊全輪詢閘門與假日判斷）
- `src/build_diag.py` → `data/diag/diag.json`（持股診斷素材庫；cache.json 走 actions/cache 不進 git）
- `src/build_mktbal.py` → `data/market_balance_history.json`（大盤餘額）
- 共用模組：`src/fmclient.py`（FinMind client＋token/台北時區）、`src/twseclient.py`（TWSE 節流）
- workflows：build/diag/mktbal/summary（各自 cron＋v2 Worker 哨兵 dispatch）＋ test.yml；
  commit/push 與失敗告警在 `.github/actions/` composite

## 不可破壞的約定（踩過坑的）

1. **TWSE 全域節流**：所有 TWSE HTTP 呼叫必須走 `twseclient.throttled_get()`。連發 ~6 次就被
   IP 限流且不自動解除（README「已知教訓」）。不要為了加速拿掉。
2. **三站同步函式**：`callClaude`/`mdToHtml`/`linkifyStocks`/`ghSaveAnalysis` 與 `sumCtx*`（gather）
   在 taiwan-flow-live-v2、taiwan-stock-news 有逐字副本，改動需三站同步；`build_summary.py` 的
   `gather_*` 是 index.html gather 的 Python 移植副本。SYS prompt 唯一事實來源＝index.html
   `SUM_SYS_POSTMKT`，`build_summary.py SYS_POSTMKT` 為移植複本需逐字同步。
3. **lending 衍生欄重建公式三處一致**：postmkt.json 的 lending.rows 只存基礎量＋px，
   衍生欄由 `index.html augmentLending()` 與 `build_summary.py _augment_lending()` 重建，
   改公式要同步（有 parity 測試守著）。
4. **XSS**：innerHTML 拼字串一律過 `esc()`；CSP meta 的 connect-src 白名單新增資料源時要同步。
5. **日期閘門**：`slot_trading_day`/`news_fresh`/`is_twse_holiday` 的跨午夜與民國年邏輯都是
   修過的生產事故，改動前先看 tests/test_summary_gates.py。
6. **金鑰**：FINMIND_TOKEN/ANTHROPIC_API_KEY 走 Actions secret；前端金鑰只存 localStorage，
   永不進 repo。持股清單只存 localStorage、不進任何網路 payload。
7. **外部消費者**：taiwan-flow-live-v2 的 Cloudflare Worker 會輪詢本 repo raw main 的
   postmkt.json/diag.json 來鏈式觸發下游；資料檔位置/欄位大改前先確認跨 repo 影響。

## 驗證方式

```bash
python -m pytest tests/ -q        # 離線單元測試（免 token/網路）
python src/build_diag.py --sample # diag 管線本地驗證（免 token）
python -m http.server 8000        # 前端本機驗證；慣例＝11 個 tab 逐一點擊 console 零 error
ruff check .                      # lint（設定在 pyproject.toml）
```

改前端後務必實測 11 tab 零 console error（歷次都這樣驗）；改 gather/SYS 後記得跨站同步檢查。
