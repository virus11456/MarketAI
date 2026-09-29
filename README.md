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

- 未啟用登入時，DeepSeek Key 存在瀏覽器 localStorage；啟用後改存依帳號區分的 sessionStorage（分頁關閉後清除），分析時隨資料傳給本站後端，再送至 DeepSeek。Groq Key 與音檔由瀏覽器直接送至 Groq。共用電腦請清除 Key；在設定輸入空白並儲存即可清除。
- Flask 收到的上傳檔在處理結束後刪除，包括處理失敗的情況。這不代表外部 AI 服務不保留資料，供應商資料政策仍需由公司確認。
- 三個 `/api/*/export` POST 端點直接回傳 DOCX 二進位檔，含 attachment 與 `Cache-Control: no-store`，不再回傳檔名 JSON，也不再提供 `/api/download/<filename>`。更新時前後端須一併部署；已開啟的舊分頁需重新整理。自行串接 API 的客戶端需同步更新。
- Colab 備援預設停用。需要使用時，管理員設定 `COLAB_API_URL=https://公司控制的服務主機`（HTTPS origin，443 埠，不含路徑、查詢參數或認證資訊），使用者輸入必須與設定一致。只允許公開 IP，並拒絕 HTTP 重新導向。ngrok URL 改變時需更新環境變數並重新部署。
- 僅設定由公司控制且可信任的 Colab 服務與 DNS；IP 檢查是額外防護，不能取代部署環境的對外連線控管。不要設定使用者可控制的反向代理服務。
- 支援下述公司 SSO，但預設停用；目前沒有專案層級權限與配額。沒有存取保護時勿配置供公開訪客使用的公司共用 AI Key。
- 月報的 KPI 由程式計算；AI 解讀與原始資料正確性仍需人工核對。

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

測試會自行在本機 5056、5057 埠啟動 Flask，模擬 AI 回覆，驗證 HTML 注入防護、自訂平台／角色互動，三種 Word 的真實匯出下載，以及公司登入頁、API 存取、CSRF、Key 儲存與登出。可設定 `PYTHON` 指定 Python 執行檔、`BROWSER_PATH` 指定既有 Chromium／Chrome 執行檔。圖片輸出在忽略追蹤的 `test-results/`。

GitHub Actions 會在 PR 與預設分支更新時執行上述測試。


## 廣告月報數據核對

現在先按「核對數據」，確認程式計算值、來源行、排除的總計列與待確認事項，再勾選覆核並產生 AI 月報。核對步驟不需要 API Key、不消耗 AI 額度。資料變更後必須重新核對。

- 支援 UTF-8 CSV（保留引號、千分位與空欄）、單張工作表 XLSX、有表頭的逗號／Tab／`|` 表格，以及每行 `花費：100` 等明確欄位和值。不支援任意自然語言推估數字、舊版 XLS 或多張報表混合加總。
- 每個平台提供同一層級、同一月份的資料。補填與來源一致的幣別及成果定義；來源有值而人工設定不同時會阻擋。
- CTR＝總點擊／總曝光×100；CPC＝總花費／總點擊；CPM＝總花費／總曝光×1000；CPA＝總花費／同一定義的總成果；ROAS＝總轉換價值／總花費。不平均各列比率，使用 Decimal 運算，顯示四捨五入至小數點後四位。
- 缺值不視為零，任一明細缺值則對應總計不產生；零分母顯示「未計算」。沒有確認幣別，不加總金額；成果定義缺少或混合時不加總成果／轉換價值，也不產生 CPA／ROAS。互動次數不會被當成點擊。
- 同時有明細與總計時只加總明細；多個總計而沒有明細會阻擋。完全相同的重複列、重複表頭、錯誤欄數或格式不明的數字也會要求修正。這不能辨識所有重疊歸因或不同層級的重複資料，仍須人工核對。
- 匯入 CTR 須明列 `%` 才能比對；原始比率與重新計算值差異超過 0.01 時列出提醒。小於容差不代表兩者的業務定義相同。
- 不產生跨平台總成果、總轉換價值或整體 ROAS，以免把多平台重複歸因當成新增成果。
- 日期欄若有提供，檢查是否位於所選月份；沒有日期欄時依賴使用者確認，無法驗證資料真的屬於該月份。來源行號對應畫面顯示的匯入文字；XLSX 轉換含工作表標記，因此不一定等於 Excel 列號。
- 程式產生的指標表與 AI 解讀分開呈現。AI 僅收到平台層級的計算結果，不收到原始檔、客戶名稱、活動明細或未驗算的原始比率；不提供素材優劣或個別活動排名。AI 文字仍可能錯誤，交付前必須覆核。
- Word 匯出使用真正的表格，保留計算值、來源行、限制與公式。

API 使用者需先 POST `/api/ad-report/validate`，再將回傳的 `fingerprint` 放入 `/api/ad-report/process` 的 `audit_fingerprint`，同時傳回相同資料與 `period_confirmed: true`。後端會重新計算並比對指紋。指紋用來避免過期核對結果，**不是身份驗證或安全簽章**。

支援欄位別名列於 `ad_metrics.py`。每次最多 12 個平台，每個平台最多 200,000 字元／5,000 行；未辨識的資料會顯示限制或要求修正，不會交由 AI 猜測。


## 公司登入設定

**預設 `AUTH_MODE=disabled`，網站仍可公開使用。合併程式不代表登入已啟用。** 正式啟用前，由管理員建立公司 OAuth 應用程式並在部署平台設定以下環境變數；不要將 Client Secret 或 Flask Secret 貼到聊天或提交 Git。

共同設定：

- `AUTH_MODE=google` 或 `microsoft`。其他值或缺少必要設定時，非靜態頁面/API 回傳 503，拒絕存取。
- `AUTH_BASE_URL` 是固定正式 HTTPS origin，例如 `https://market-ai-sepia.vercel.app`，不能含路徑。OAuth redirect URI 必須精確登記為 `https://market-ai-sepia.vercel.app/auth/callback`（換成自己的 origin）。
- `OIDC_CLIENT_ID`、`OIDC_CLIENT_SECRET` 為提供者核發的 Web 應用程式憑證。
- `FLASK_SECRET_KEY` 至少 32 字元，使用隨機值，例如在可信任管理終端執行 `python -c "import secrets; print(secrets.token_urlsafe(48))"`。所有執行個體使用相同值。

Google Workspace：在 Google Cloud 建立 OAuth Web application，設定上述 callback，並在 `AUTH_GOOGLE_DOMAINS` 填入允許的公司網域（逗號分隔）。授權檢查已驗證 ID token 的 `hd` 和 `email_verified`，不是僅比較信箱字尾；個人 Gmail 不會通過。[Google 官方 OIDC 文件](https://developers.google.com/identity/openid-connect/openid-connect)。

Microsoft 365：在 Entra 建立單一租用戶 Web application，設定 callback 與 Client Secret；`AUTH_MICROSOFT_TENANT_ID` 填 tenant UUID。在 App registrations → App roles 建立允許 Users/Groups 的角色，value 為 `MarketAI.User`（或同步修改 `AUTH_MICROSOFT_ROLE`），再於 Enterprise applications 將角色指派給允許的使用者／群組。只有 tenant 相符且 token 含指定角色才允許登入；信箱與姓名僅供顯示。租用戶內訪客若獲指派角色也會獲准，請由管理員控管指派。[Microsoft 應用程式角色文件](https://learn.microsoft.com/en-us/entra/identity-platform/howto-add-app-roles-in-apps)。

設定後重新部署，以允許帳號確認三項工具與 Word 匯出，再以未授權帳號確認拒絕登入。未登入 API 應回傳 401；設定缺漏應回傳 503。真實提供者往返仍須在公司環境驗收，測試套件只模擬提供者網路回應並驗證實際簽章 Token。開發時只允許非 Vercel 的 `http://localhost:埠`／`http://127.0.0.1:埠`，同樣需精確登記 callback。

啟用後的行為與限制：

- 所有工具/API 需要登入；靜態檔與登入流程保持可讀。POST API 需登入 cookie 與頁面的 `csrf-token` meta 所提供的 `X-CSRF-Token`，前端會自動加入；跨來源請求不攜帶此 Token。
- OAuth 使用 state、nonce、PKCE，以及簽章、issuer、audience、有效期限驗證；不在工作階段保存提供者 access/refresh/ID token。Cookie 使用 HttpOnly、SameSite=Lax，正式 HTTPS 下啟用 Secure。
- 登入最長一小時（若 ID token 更早過期則縮短），不自動延長。Cookie 是簽章的無伺服器狀態工作階段：移除公司角色／帳號不會立即撤銷已發出的 cookie，最長需等一小時。輪替 Flask Secret 可撤銷所有工作階段；變更登入授權設定也會使既有工作階段失效。
- 啟用後會清掉舊 localStorage 的 AI Key，需要重新輸入；新 Key 依帳號存於目前分頁。登出會清除此瀏覽器 MarketAI Key，並通知其他已開啟分頁清除。此為本系統登出，Google／Microsoft 登入狀態可能仍保留。
- 尚無個別工作階段撤銷、專案權限、稽核資料庫或使用配額；這些需要另外實作。
