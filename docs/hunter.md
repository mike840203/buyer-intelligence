# Hunter.io 使用指南

## 這是什麼、為什麼用它

Hunter 是 email 查找與驗證服務。本系統只用它的 **Email Verifier**:
在寄信前確認信箱有效。

**為什麼這步不能省**:冷開發信的退信率(bounce rate)直接影響寄件網域
的信譽分數。退信率一超過約 2–5%,郵件服務商就會開始把你的所有信件
丟進垃圾桶——一批爛名單可以毀掉整個網域,之後怎麼寫都沒人看到。
所以 L2 清洗層的鐵律:**未驗證的 email 不進 outreach**。

在系統中的位置:`enrich.py` 的 `verify_email()`,
L2 清洗時自動對每筆 lead 的 email 呼叫,結果寫入 `Lead.email_verified`。

## 申請步驟

1. 到 <https://hunter.io> 註冊(免費方案即可起步)
2. 登入後右上角頭像 → **API** → 複製 API key
   (或直接開 <https://hunter.io/api-keys>)
3. 填入 `.env`:
   ```
   HUNTER_API_KEY=你的金鑰
   ```

## 方案與額度

- **免費方案**:每月提供少量免費驗證額度,足夠驗證期的小名單
- **付費方案**:依驗證量計價,月費從數十美元起
- 確切額度與價格常調整,以 <https://hunter.io/pricing> 為準
- 替代品:NeverBounce、ZeroBounce(架構報告點名的備案;
  `verify_email()` 只有十餘行,要換服務改一個函式即可)

## 系統內用法

不需要手動呼叫——`ingest` 匯入名單時自動執行:

```bash
buyer-intel ingest --source manual --file 名單.csv
# 每筆有 email 的 lead 會自動打 Hunter 驗證
```

程式位置:`src/buyer_intel/enrich.py` → `verify_email()`
- 呼叫端點:`GET https://api.hunter.io/v2/email-verifier?email=...&api_key=...`
- 判定為有效的狀態:`valid`、`accept_all`、`webmail`
- **未設金鑰時的行為**:不阻擋流程,一律標 `email_verified=False`
- 驗證失敗(網路錯誤等)同樣標 False,不會中斷匯入

`email_verified=False` 的意義:該 lead 仍會被評分與產草稿,
但你在 `buyer-intel review` 覆核時應特別注意——寄給未驗證信箱
風險自負。

## 疑難排解

| 症狀 | 原因 | 解法 |
|---|---|---|
| `401` | 金鑰錯誤 | 到 API keys 頁面重建 |
| `429` | 超出當月額度或速率限制 | 等額度重置或升級;大批名單分批 ingest |
| 大量 `accept_all` | 對方郵件伺服器來者不拒,無法確認個別信箱 | 系統視為可寄,但實際退信風險略高,量大時可改列灰名單 |
| 驗證都是 False | 多半是沒設金鑰 | 檢查 `.env` 的 `HUNTER_API_KEY` |

## 費用注意

- 驗證按「次」計,**同一 email 不要重複驗**——系統匯入時已去重,
  重跑 `ingest` 同一檔案不會重複入庫(也就不會重複驗證)
- 名單量產期(每月數百筆)大概率需要付費方案,把它當作
  網域信譽的保險費
