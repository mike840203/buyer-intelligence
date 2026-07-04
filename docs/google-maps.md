# Google Maps Places API 使用指南

## 這是什麼、為什麼用它

Places API 是 Google 地圖的店家資料庫查詢介面。本系統用它的
**Text Search**:按「類別 + 城市」掃出實體店家名單。

**為什麼需要它**:戰略主攻的 T1 通路有一大塊是**獨立精品咖啡店與
獨立廚具零售商**——這類小商家在 Apollo 等 B2B 資料庫的覆蓋很差
(沒有上市、沒有 LinkedIn 主頁、員工數兩位數以下),但它們幾乎
100% 有 Google 商家檔案。這是掃出「主街上的真實店家」唯一可靠的來源。

在系統中的位置:**L1 資料擷取層**(`adapters/places.py`)。
注意它只能拿到**店名、地址、網站**——拿不到聯絡人與 email,
所以流程是兩段式(架構報告 L1 明訂):

```
Places 掃店名/網域 → 回頭用 Apollo(付費)或 Hunter Domain Search 找決策人 email
```

## 申請步驟

Google Cloud 的申請比前兩者繁瑣,照順序做:

1. 到 <https://console.cloud.google.com> 登入 Google 帳號
2. 建立專案(上方專案選單 → New Project → 命名如 `buyer-intel`)
3. **啟用 API**:左側選單 → APIs & Services → Library →
   搜尋 **Places API (New)** → Enable
   (注意選 **New** 版本,系統用的是新版端點)
4. **建立金鑰**:APIs & Services → Credentials → Create Credentials
   → API key → 複製
5. **限制金鑰**(強烈建議,防盜刷):點金鑰 → API restrictions →
   Restrict key → 只勾 Places API (New)
6. **綁定帳單**:Billing → 綁信用卡(沒有帳單帳戶 API 會拒絕請求;
   有免費額度,小量使用實際不會扣款,見下)
7. 填入 `.env`:
   ```
   GOOGLE_MAPS_API_KEY=你的金鑰
   ```

## 方案與額度

- Places API 按呼叫次數計價,Google 提供**每月免費用量額度**
- 本系統的用法(掃 5 個目標城市 × 每城市數十筆)每月僅需
  數十到數百次呼叫,**遠低於免費額度,實際成本趨近 $0**
- 確切費率與免費額度以官方為準:
  <https://mapsplatform.google.com/pricing/>
- 保險起見可在 Billing → Budgets & alerts 設每月 $10 預算警示

## 系統內用法

```bash
# 掃西雅圖的精品烘豆商
buyer-intel ingest --source places --query "specialty coffee roaster in Seattle, WA"

# 掃奧斯汀的獨立廚具店(T2 通路)
buyer-intel ingest --source places --query "kitchenware store in Austin, TX" --tier T2_kitchen
```

建議的掃描組合(對應戰略地區優先序):

| 優先序 | 城市 | query 範例 |
|---|---|---|
| P1 | Seattle, Portland | `specialty coffee roaster in Seattle, WA` |
| P1 | Austin, Houston, Dallas | `specialty coffee shop in Austin, TX` |
| P2 | SF Bay Area, LA | `coffee equipment store in San Francisco, CA` |
| P2 | NYC | `kitchenware store in New York, NY` |

程式位置:`src/buyer_intel/adapters/places.py`
- 呼叫端點:`POST https://places.googleapis.com/v1/places:searchText`
  (Places API New 的 Text Search)
- 金鑰放 `X-Goog-Api-Key` header;`X-Goog-FieldMask` 只要求
  店名/地址/網站三個欄位(**FieldMask 要得越少計價越低**,勿隨意加欄位)
- 回傳映射為 `RawLead`:店名 → company、網站 → website、
  從地址抽州別 → state(供地區評分),notes 註記「需回頭找決策人」

## 疑難排解

| 症狀 | 原因 | 解法 |
|---|---|---|
| `403 PERMISSION_DENIED` | 沒啟用 Places API (New),或金鑰限制擋到 | Library 確認啟用的是 New 版;檢查金鑰的 API restrictions |
| `400 INVALID_ARGUMENT` | FieldMask 或 query 格式錯 | query 用自然語言「類別 in 城市, 州」格式 |
| `REQUEST_DENIED` + billing 字樣 | 專案沒綁帳單帳戶 | Billing 綁卡 |
| 回傳店家數少 | Text Search 單次上限 20 筆 | 換更細的 query(分區、分類別)多掃幾次 |
| state 抽取為空 | 地址格式特殊 | 無妨,region 會落 OTHER,可在覆核時手動改 |

## 費用注意

- 每次 `ingest --source places` = 1 次 Text Search 呼叫(≤20 筆結果)
- 即使掃完全部目標城市,總呼叫量也在兩位數——**這是三個工具裡
  最不用擔心費用的**,前提是金鑰有做 API 限制、不外洩
