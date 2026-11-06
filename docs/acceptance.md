# 實際驗收紀錄

驗收日期：2026-11-06；Windows / PowerShell 7.6.5，E 槽實際存在。

目標專案：`E:\TradingLab`。初始目錄不存在，已建立 Git 儲存庫，沒有刪除或覆蓋使用者既有專案。編輯工作區 `C:\Users\Admin\OneDrive\文件\ChatGPT\TRADELAB` 保留同步來源；日常請以 E 槽專案為準。

## 已實作並測試

| 驗收項目 | 證據 |
|---|---|
| 環境、安裝、migration、首次 DEMO 資料 | 已於 E 槽執行 setup.ps1，Python 3.12.13 專用 venv、npm ci、Alembic upgrade head、固定 seed 初始化成功 |
| 四個獨立程序 | start-dev.ps1 實際啟動 backend/risk/watchdog/research；儀表板顯示各自心跳 |
| 安全停止／重啟 | stop-dev.ps1 實際停止自己建立的程序，再次 start-dev.ps1 成功，保留帳戶與資料 |
| CSV 匯入與品質 | API 及瀏覽器實測；重複／逆序日期、不合理 OHLC、NaN、負數量測試通過 |
| TWSE adapter | 0050、20250101 月日線實際回傳 HTTP 200；快取保留在 data/cache；未改變原 DEMO 模式 |
| 回測 | HTTP 與瀏覽器完成，顯示策略／基準曲線、費前費後、回撤、勝率、平均盈虧、PF、次數、曝險、換手與交易清單；不修改運作帳戶 |
| 合法委託 | 瀏覽器人工提案 → 獨立風控核准 → 人工確認 → 推進下一日開盤成交 |
| 部分成交、保留款與撤單 | 初始 0050 90 股、48.50 元提案，次日成交 56 股、47.29 元，最低手續費 20 元；撤單後保留款歸零；結果為明確合成 DEMO |
| 超額／超賣／資金不足／過期行情 | 自動測試驗證 PER_ORDER、SYMBOL_EXPOSURE、TOTAL_EXPOSURE、OVERSELL、CASH、STALE_QUOTE、MARKET_CLOSED |
| 並行額度 | 6 個並行風控請求；保留額度原子化，不能共同超過單標的限制 |
| 核准完整性、時效、一次使用 | 修改 payload、過期核准拒絕；consume 與 gateway 重送冪等 |
| 服務故障 | 真實終止 risk 程序，新提案 HTTP 503；真實終止 watchdog，心跳逾時拒絕新風險 |
| 重啟與重複成交 | 真實 backend 重啟後重送，現金與成交次數不變；unique(order,bar) 保護 |
| 對帳與持久鎖定 | 注入 1 元帳差，watchdog 記錄證據並鎖定；backend 重啟鎖定仍存在；稽核鏈修改亦告警 |
| DAY 委託 | 下一開盤未完成餘額，到下一步會到期撤銷與釋放保留額 |
| 獨立研究 | 實際 worker 完成候選模型、四段時間切分、5 日 gap；重複 seed 結果一致；候選不自動上線 |
| 模型啟用界線 | 未達門檻不可核准；重播早於訓練標籤完成時間不可核准；正確時點後人工核准只改模擬策略 |
| 研究資料去重 | 同樣內容改來源名稱仍是同資料版本，不能藉改名重複使用 holdout |
| 已實現模型績效 | FIFO 進場模型歸因；未達 30 筆顯示 N/A，自動測試確認不能硬填勝率 |
| 認證與 LIVE | 無權杖 401、惡意 Origin 403、approved=true 額外欄位拒絕、直接 LIVE API 403、LiveBroker 拋出禁用錯誤 |
| 前端 | Vue TypeScript 檢查與 Vite production build 通過；Vitest 圖表空資料與有限座標測試 2/2 通過 |
| Chromium E2E | 1 個完整工作流程測試通過，覆蓋八頁相關操作；無瀏覽器 pageerror；桌面 1440px 與手機 390px 截圖檢查 |
| 備份與還原 | SQLite backup API 實際產生 data/acceptance-backup.sqlite3；另在隔離資料庫演練備份、修改、還原及對帳成功 |
| 未知狀態 | 注入 consume 已完成但回應遺失，gateway 保存 UNKNOWN；人工核對後恢復 ACCEPTED，不重複成交 |
| 排程去重 | 重複觸發同資料版本排程，工作 ID 與數量保持不變 |

後端驗收：`python -m pytest -q`，**25 passed**。元件：`npm run test:unit`，**2 passed**。瀏覽器：`npm run test:e2e`，**1 passed**。前端：`npm run build` 成功。

Playwright 報告：`frontend/playwright-report/index.html`；截圖：`artifacts/dashboard-desktop.png`、`artifacts/dashboard-mobile.png`。這些為本機驗收產物，不放入 Git。

## 已實作但未完整驗證

- 備份還原已用隔離資料驗證；尚未做大量資料及檔案系統損毀的復原壓力測試。
- 研究排程及持久去重已實作並測試；尚未做數日長時間排程／斷電壓力測試。
- 模型 30／60 筆門檻與近期績效變化計算已實作；單筆實現歸因已測，尚無足夠真實市場結果驗證其研究價值。
- 公開來源已成功下載單月 0050 日線；未全面驗證每檔股票、每年度、所有 TWSE 例外資料與外部長期可用性。
- UNKNOWN 訂單持久狀態與人工核對已實作；重送與服務斷線已測，但沒有真實券商可供驗證外部受理後網路中斷的核對流程。

## 尚未實作／刻意停用

- 真實券商 adapter、LIVE 下單（後端硬性停用）。
- 已驗證的即時行情與即時 PAPER 模式（停用，不冒充即時交易）。
- 官方全年度交易日曆、停牌、開盤競價基準與真實漲跌停驗證；本版只按資料中的明確交易日推進。
- 公司行動／股息再投資／總報酬還原、實際 T+2 款券交割。
- 逐筆撮合、盤中零股撮合時間序列、委託簿排隊及同根 K 棒內停利停損路徑。
- PostgreSQL 實際 migration 與列鎖、獨立主機部署及惡意本機管理者防護。

上述範圍均在 README 與畫面標示。系統沒有偽造新聞、即時行情、績效或獲利保證。
