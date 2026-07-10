# Gmail API 寄送後端設定指南

`SENDING_BACKEND=gmail` 時,系統核准後的信會由排程器透過 Gmail API 自動寄出。
預設是 `eml` 乾跑模式(輸出 .eml 由人寄)——**建議網域/帳號準備好之前都留在乾跑**。

## 開始前:帳號策略(比設定步驟更重要)

1. **不要用私人主帳號**。開一個專用 outreach 帳號,萬一被 Gmail 風控,影響範圍小。
2. **認真做就上 Google Workspace + 專用寄信網域**(勿用公司主網域),
   設好 SPF / DKIM / DMARC 三個 DNS 紀錄後暖機 4–6 週再正式開跑。
3. 寄第一封真實買家前,先到 <https://www.mail-tester.com> 免費測寄件分數,
   低於 7 分先處理 DNS 認證再說。

## Gmail 的兩層限制(系統已內建對應防線)

| 層 | 內容 | 系統防線 |
|---|---|---|
| 硬上限 | 免費帳號約 500 收件人/天、Workspace 約 2000 | 量級差太遠,碰不到 |
| 信譽風控 | 新帳號爆量、被標 spam、高退信、無 DNS 認證 → 進垃圾桶/限寄 | warmup 每日上限、一次 1 封、60–90 分錯開、工作日時段、3 封上限、回覆即停、footer 退訂、bounce 自動退訂 |

## OAuth 憑證申請(約 15 分鐘,一次性)

1. 到 [Google Cloud Console](https://console.cloud.google.com) 建專案 →
   「API 和服務」→ 啟用 **Gmail API**
2. 「OAuth 同意畫面」:User type 選 External,填 app 名稱;
   Scopes 加 `https://www.googleapis.com/auth/gmail.send` 與
   `https://www.googleapis.com/auth/gmail.readonly`(回覆偵測用);
   Test users 加你的 outreach Gmail
3. 「憑證」→ 建立 OAuth 用戶端 ID → 類型選「電腦版應用程式」→
   記下 **Client ID** 與 **Client Secret**
4. 換 **Refresh Token**(用 [OAuth 2.0 Playground](https://developers.google.com/oauthplayground)):
   - 右上齒輪 → 勾「Use your own OAuth credentials」→ 填入上面的 ID/Secret
   - 左側輸入 scope:`https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly`
   - Authorize → 用 outreach 帳號登入同意 → Step 2 按「Exchange authorization
     code for tokens」→ 複製 **Refresh token**
5. 三個值填進 `.env`:

```bash
SENDING_BACKEND=gmail
GMAIL_CLIENT_ID=xxx.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=xxx
GMAIL_REFRESH_TOKEN=xxx
```

6. 重啟 `buyer-intel serve`,/outbox 頁後端顯示「🟢 Gmail 自動寄送」即接通。

## 系統在 gmail 模式多做的事

- 寄出必須拿到 Gmail message id 才標 `sent`,失敗標 `failed`(不謊報)
- seq2/3 帶 `threadId` 接第一封的對話串(對方看到的是同一串,不是新信轟炸)
- 排程器每輪自動**偵測回覆**:thread 裡出現不是我們寄的訊息 →
  自動取消該 lead 剩餘跟進、推進階段為 followed_up
- 同網域累積 3 次寄送失敗 → 整網域自動加入退訂名單(保護寄件信譽)
- company profile 的 `sender.address` 沒填真實地址時,自動寄送會被擋下
  (CAN-SPAM 必填實體地址)

## 疑難排解

| 狀況 | 處理 |
|---|---|
| `invalid_grant` | Refresh token 失效(測試模式 7 天過期):OAuth 同意畫面按「發布應用程式」轉正式,或重新走 Playground 換 token |
| 403 `accessNotConfigured` | Gmail API 沒啟用,回 Cloud Console 啟用 |
| 寄出但進對方垃圾桶 | DNS 認證問題:SPF/DKIM/DMARC + mail-tester 檢測 |
| 想暫停自動寄送 | /outbox 頁「⏸ 暫停自動寄送」;佇列保留,恢復後繼續 |
