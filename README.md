# MarketAI

行銷團隊的廣告月報、會議記錄及報價單工作拆分工具。Python Flask + 原生 JavaScript；DeepSeek 負責內容整理，Groq Whisper 負責語音轉文字。

## 本機執行

需要 Python 3.10 以上（CI 使用 3.12）。

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
flask --app app run
```

在瀏覽器的「API 設定」填入自己的 Key。`.env.example` 內的 Key 是佔位符；使用前端 Key 時請將 `.env` 中未使用的 Key 清空。不要把 `.env` 或真實 Key 提交到 Git。

## 資料與部署注意事項

- DeepSeek Key 存在瀏覽器 localStorage，分析時隨資料傳給本站後端，再送至 DeepSeek。Groq Key 與音檔由瀏覽器直接送至 Groq。共用電腦請清除 Key；在設定輸入空白並儲存即可清除。
- Flask 收到的上傳檔在處理結束後刪除，包括處理失敗的情況。這不代表外部 AI 服務不保留資料，供應商資料政策仍需由公司確認。
- 三個 `/api/*/export` POST 端點直接回傳 DOCX 二進位檔，含 attachment 與 `Cache-Control: no-store`，不再回傳檔名 JSON，也不再提供 `/api/download/<filename>`。更新時前後端須一併部署；已開啟的舊分頁需重新整理。自行串接 API 的客戶端需同步更新。
- Colab 備援預設停用。需要使用時，管理員設定 `COLAB_API_URL=https://公司控制的服務主機`（HTTPS origin，443 埠，不含路徑、查詢參數或認證資訊），使用者輸入必須與設定一致。只允許公開 IP，並拒絕 HTTP 重新導向。ngrok URL 改變時需更新環境變數並重新部署。
- 僅設定由公司控制且可信任的 Colab 服務與 DNS；IP 檢查是額外防護，不能取代部署環境的對外連線控管。不要設定使用者可控制的反向代理服務。
- 應用程式目前尚未實作登入、專案權限與配額。公司部署必須另設 SSO／存取保護；沒有存取保護時勿配置供公開訪客使用的公司共用 AI Key。
- 本輪不包含 KPI 計算引擎；AI 月報的數字與跨平台轉換定義仍需人工核對。

## 回歸測試

後端測試不用真實 AI Key，也不呼叫外部服務：

```sh
python -m unittest discover -s tests -v
```

瀏覽器測試需要 Node.js 24 與 pnpm 10 以上：

```sh
pnpm install --frozen-lockfile
pnpm exec playwright install chromium
pnpm test
```

測試會自行在本機 5056 埠啟動 Flask，模擬 AI 回覆，驗證 HTML 注入防護、自訂平台／角色互動，以及三種 Word 的真實匯出下載。可設定 `PYTHON` 指定 Python 執行檔、`BROWSER_PATH` 指定既有 Chromium／Chrome 執行檔。圖片輸出在忽略追蹤的 `test-results/`。

GitHub Actions 會在 PR 與預設分支更新時執行上述測試。
