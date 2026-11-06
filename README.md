# TradingLab

繁體中文台股策略研究、歷史重播與模擬交易工作區。**不是自動獲利系統，不提供真實下單。** 預設 0050、006208、2330 為合成資料研究標的，非投資推薦。

實際安裝位置：`E:\TradingLab`。Windows PowerShell、Node.js 24.16.0、Python 3.12.13；不需要 Docker、Oracle、Redis、GPU、LLM API 或付費服務。

## 啟動

本機已完成安裝。開啟 PowerShell：

```powershell
cd E:\TradingLab
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

開啟 **http://127.0.0.1:8765**。將腳本顯示的權杖貼入登入視窗；也可讀取 `.local\client-token.txt`。權杖只存於分頁 sessionStorage，不編入前端 bundle。請勿分享 `.local`。

若顯示已有啟動紀錄，表示服務可能正在執行，直接開啟網址即可。停止：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop-dev.ps1
```

只向本專案 supervisor 發送停止要求，再由它終止自己建立的子程序；不搜尋或終止全部 Python／Node。`-ExecutionPolicy Bypass` 僅適用這次 PowerShell 程序，不永久修改全域政策。

## 全新安裝

需 Node.js 24 LTS，以及 Python 3.12 或 uv。若已有 uv，setup 會下載專案專用 Python，不改系統 Python。

```powershell
cd E:\TradingLab
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

setup 使用 `requirements.lock` 與 `frontend/package-lock.json`，執行 Alembic migration、建立固定 seed 的 540 日示範資料、產生不同用途的隨機權杖及建置前端。重跑不重設帳戶或覆蓋使用者資料。

正式建置前端由後端在 8765 提供。開發 Vue 熱更新另開終端執行 `cd frontend; npm run dev`，使用 http://127.0.0.1:5173。風控獨立在 8766；watchdog、research 也是獨立 OS 程序。

## 第一次操作

1. 儀表板確認 backend、risk、watchdog、research 都有心跳，模式是 DEMO。
2. 策略與提案：可計算 MA5/MA20 訊號，或人工建立 0050 買進 90 股、限價 48.50 的示範提案（適用**首次預設日期** 2023-04-03）。行情推進後應重新決定合理限價。
3. 「送出風控審核」後，在 30 秒內按「確認模擬委託」。此時只保留資金，還沒成交。
4. 回儀表板按「推進一個交易日」。首次範例受成交量參與率限制只成交 56 股；模擬帳戶可檢查成交價、手續費與剩餘保留款，再撤單。這些是可重現的模擬結果。
5. 回測實驗室選資料與參數，執行後查看曲線、基準、完整交易列表。研究中心可啟動邏輯迴歸批次研究，候選未通過門檻時保留原策略。
6. 減倉需從已確認持倉準備賣出提案，再審核及確認；停止新增風險不等同市價清倉。

驗收時可能留下清楚標示的 DEMO 委託、研究與匯入紀錄；不會偷偷清除。可先備份，再以新的 `TRADINGLAB_DATA` 與 `TRADINGLAB_LOCAL` 目錄建立完全獨立實驗環境。

## CSV 與公開資料

CSV 必須 UTF-8，日期嚴格遞增、不重複，股數為整數，OHLC 必須合理且有限：

```csv
date,open,high,low,close,volume
2024-01-02,40,42,39,41,20000
2024-01-03,41,43,40,42,30000
```

日期視為 Asia/Taipei 的日線交易日。缺欄、空價格、NaN、負成交量、重複／逆序日期皆拒絕。缺少整個日期可能是休市或停牌，沒有官方日曆不能自動認定為缺資料，因此以警告標示，不填補假行情。

「TWSE 公開日線」以月份抓取 STOCK_DAY，快取在 `data/cache`，最短請求間隔 10 秒。失敗回傳錯誤，不會偷偷切成 DEMO。資料版本不可變，以 SHA-256 識別；來源、資料時間、接收時間、調整狀態都保存。

還原價格可研究但不能啟用委託重播。缺少公司行動與股息資料，跨除權息回測不是總報酬。REPLAY 僅使用資料內日期，不把週一到週五當成完整交易日曆。PAPER 因未整合已驗證即時來源而停用，LIVE 後端固定拒絕。

## 測試

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm run build
npm run test:unit
npx playwright install chromium
npm run test:e2e
```

pytest 自動使用暫存資料庫；程序測試使用 8865/8866，實際停止／重啟自己建立的服務。不要同時跑多組相同 port 的程序測試。瀏覽器端測試另用 8875/8876 與獨立資料；不操作日常模擬帳戶。

驗收細節見 [docs/acceptance.md](docs/acceptance.md)。架構、資料表與 API 見 [docs/architecture.md](docs/architecture.md)。市場來源與工程假設見 [docs/market-rules.json](docs/market-rules.json)。

## 備份、還原與 migration

```powershell
.\.venv\Scripts\python.exe -m scripts.backup backup E:\TradingLab\backup-20261106.sqlite3
# 另備份 data/models（模型產物）與 .local/secrets.json（私密，勿上傳 Git）
.\scripts\stop-dev.ps1
.\.venv\Scripts\python.exe -m scripts.backup restore E:\TradingLab\backup-20261106.sqlite3
.\.venv\Scripts\python.exe -m alembic upgrade head
.\scripts\start-dev.ps1
```

備份使用 SQLite backup API，支援 WAL 一致性快照。還原須先停止所有服務，會保留 `.pre-restore.sqlite3`。遷移只有升級；破壞性 downgrade 禁止，以已驗證備份回復。SQLAlchemy 隔離資料存取，但目前交易鎖、JSON 查詢與 SQL 方言針對 SQLite；PostgreSQL 遷移仍需改為列鎖與對應 migration，不能只改連線字串。

## 常見問題

- **登入 401**：權杖須對應目前 `.local/client-token.txt`；更換資料環境或權杖後重新貼入。
- **服務鎖定**：先看風控與監察的原因、證據；修復服務、核對未知訂單後，填至少 8 字人工確認紀錄解除。重啟不解除鎖定；確認告警也不解除鎖定。
- **心跳過期**：查看 `.local/backend.log`、`risk.log`、`watchdog.log`、`research.log`。未確認服務安全前買進會被阻擋。
- **埠被佔用**：不要終止其他程式；先確認是否同一專案已啟動。預設固定 8765/8766，開發前端 5173。
- **Supervisor 非正常中斷**：查 `.local/supervisor.json` 所列 PID、程式路徑與命令列是否確實屬於本專案；不要單憑 PID 終止程序。確認本專案子程序都停止後，把該紀錄移到備份檔名，再啟動。
- **研究 SKIPPED**：少於 250 筆或此資料版本保留測試集已使用；屬正常保護，不能反覆拿同一測試集選模。
- **無法成交**：只有下一交易日開盤加滑價符合限價且有容量才成交。不是按下送出就用當下收盤價成交。
- **整股示範不成交**：預設成交量參與率 0.1% 加上 5% 單筆上限通常適合零股；不會將 50 股當成 50 張。

## 明確限制與後續路線

這是同機程序隔離，無獨立主機或惡意管理者防護能力。共用本機 SQLite 與服務認證，管理者仍能讀秘密、改資料與程式。交易風控只能阻擋／告警，不保證實際虧損不越界。

第一版為日線研究：無逐筆委託簿、盤中撮合、官方全年度日曆、停牌／漲跌停基準整合、公司行動總報酬調整與真實 T+2 交割。DAY 委託僅嘗試下一日開盤，下一步未完成餘額自動到期撤銷；不模擬棒內停損停利先後。買進持有基準與策略都只使用單一相同比例資金區塊，不能與全資金投入的市場報酬混比。

研究模型僅以人工觸發或去重排程訓練。重播日期必須晚於訓練標籤完成時間才能人工啟用，避免未來資訊洩漏。已實現績效按進場模型歸因 FIFO 批次；至少 30 個已平倉批次才顯示統計，60 個才評估近期績效變化，樣本不足標示 N/A。另提供特徵分布與缺漏率警告，不自動換模。後續優先補齊公司行動與官方日曆、真實交割／更多撮合情境、PostgreSQL 列鎖與獨立主機部署，再評估券商整合。
