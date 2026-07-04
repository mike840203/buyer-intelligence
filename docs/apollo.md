# Apollo.io 使用指南

## 這是什麼、為什麼用它

Apollo 是 B2B 銷售情報資料庫:收錄數億筆商業聯絡人,可用
**職稱 × 產業 × 地區** 組合搜尋,並直接提供工作 email。

在本系統中是 **L1 資料擷取層的主力名單來源**(`adapters/apollo.py`):
目標是找出「美國零售商裡有採購決策權的人」——Buyer、Category Manager、
Merchandising Manager、Owner。

選 Apollo 而非 ZoomInfo 等企業級工具的理由:入門成本低
(ZoomInfo 年約數千美元且要走業務流程)、低價方案就開放 API、
有免費 tier 可先熟悉資料品質。

## 申請步驟

1. 到 <https://www.apollo.io> 註冊(用公司 email 較不易被限制)
2. 登入後:左下角頭像 → **Settings** → **Integrations** → **API**
   (或直接開 <https://app.apollo.io/#/settings/integrations/api>)
3. **Create API Key** → 命名(例如 `buyer-intel`)→ 複製金鑰
4. 填入專案根目錄 `.env`:
   ```
   APOLLO_API_KEY=你的金鑰
   ```

## 方案與額度(⚠️ 含 2026-07 實測事實)

| 端點 | 免費方案 | 付費方案(Basic 起) |
|---|---|---|
| `mixed_people/search`(找決策人)| ❌ **403 API_INACCESSIBLE**(實測) | ✅ |
| `people/match`(聯絡人補全)| ❌ 403(實測) | ✅ |
| `organizations/enrich`(公司補全)| ✅ **可用**(實測 200) | ✅ |
| 網頁介面搜尋 + 匯出 CSV | ✅(匯出有額度限制) | ✅ |

**重點:免費方案拿不到「API 找人」**——這是帳號方案等級的限制,
換金鑰無效。付費方案 Basic 約 $49–59/月起(以
<https://www.apollo.io/pricing> 為準)。

### 免費方案的兩條替代路

1. **網頁搜尋 → 匯出 CSV → 手動匯入**(目前採用):
   - 在 Apollo 網頁用 People 搜尋:Job Titles 填
     `Buyer, Category Manager, Merchandising Manager, Owner`,
     Location 填 `Washington, US / Oregon, US / Texas, US`,
     關鍵字 `specialty coffee` 或 `kitchenware`
   - Export → CSV,**不需要改欄位名**——`manual` adapter 會自動識別
     Apollo 匯出格式(First/Last Name 自動合併、Company/State 自動映射、
     Industry 與員工數收進備註)
   - 匯入(擇一):
     - **Web UI(建議)**:`buyer-intel serve` → http://localhost:8000/import
       直接上傳,去重明細顯示在結果頁
     - CLI:檔案放 `imports/` → `buyer-intel ingest --source manual --file imports/匯出檔.csv`
2. **公司補全 API**(免費可用,尚未接入系統,需要時可加):
   以網域查公司規模/產業/LinkedIn,適合補強 L2。

## 系統內用法(付費方案開通 API 後)

```bash
buyer-intel ingest --source apollo --query "specialty coffee equipment retailer"
```

程式位置:`src/buyer_intel/adapters/apollo.py`
- 預設職稱清單:`DEFAULT_TITLES`(Buyer / Category Manager / …)
- 預設地區:`Washington, US / Oregon, US / Texas, US`(對應戰略 P1)
- 呼叫端點:`POST https://api.apollo.io/api/v1/mixed_people/search`,
  金鑰放 `X-Api-Key` header
- 每筆結果映射為 `RawLead`(公司、姓名、職稱、email、城市、州)

## 疑難排解

| 症狀 | 原因 | 解法 |
|---|---|---|
| `403 API_INACCESSIBLE` | 免費方案不開放該端點 | 升級方案,或走網頁匯出 CSV |
| `401` | 金鑰錯誤/被撤銷 | 到 API 設定頁重建金鑰 |
| `422` | 查詢參數格式錯 | 檢查 `person_locations` 格式(`"Texas, US"`) |
| 回傳有人但 email 是 null | Apollo 對部分聯絡人要求額外 credit 揭露 email | 網頁版點開揭露,或提高方案 |
| 名單重複 | 正常 | 系統匯入時自動去重(公司名模糊比對 + email domain) |

## 費用注意

- API 有速率限制(依方案不同,每分鐘/每小時/每日),
  `ingest` 一次抓 25 筆的預設值遠低於限制
- email export credit 是月配額,量產期注意用量儀表板
