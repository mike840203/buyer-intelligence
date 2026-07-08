# ImportYeti 使用指南

## 這是什麼、為什麼對 Ankomn 特別重要

美國海關規定:所有海運進口的**提單(Bill of Lading)是公開資料**——誰(美國公司)
從誰(海外供應商)進口了什麼、幾櫃、什麼時候。ImportYeti 把這批公開數據做成
**免費**的搜尋引擎(Panjiva / ImportGenius 賣的是同一批數據的企業版,年費數千美元,
驗證期不需要)。

**它給的訊號是其他所有來源都給不了的:「這家公司有進口能力」。**

對 Ankomn 的三個戰略價值:

1. **找 T0 經銷商的最短路徑**:會從亞洲進口廚房/家居用品的美國公司 =
   有進口、清關、倉儲能力的公司 = 現成的經銷商候選。戰略報告明訂
   「經銷商解決進口、倉儲與物流,適合尚無美國倉的階段」——這正是找他們的工具
2. **逆向工程競品的通路**:搜 Fellow、Planetary Design 等競品公司名,
   看誰在幫他們進口/收貨——那些收貨方(consignee)已經被競品教育完成
3. **開發信的黃金切入點**:「注意到貴司有從亞洲進口家居用品的紀錄,
   我們是台灣製造商,正在尋找美國經銷夥伴…」——具體、真實、直指對方能力

## 免費 vs 付費:你只需要免費的部分

| 功能 | 費用 | 你需要嗎 |
|---|---|---|
| **網頁搜尋、看 shipment 紀錄、看 consignee** | **免費(free-forever)** | ✅ 這就是全部所需 |
| API 存取、大量批次匯出(bulk export) | 付費 custom plan | ❌ 不需要 |

**為什麼不需要 API**:你要找的是 T0 經銷商候選,量級是**幾十家**,不是幾千家。
幾十家用網頁一家家看、手抄整理,一個下午就夠——付費 API 是給要程式化拉
整批數據的公司用的,不是你的使用情境。(跟 Apollo 一樣:API 付費牆擋著,
但你要的量級手動網頁就夠。)

## 申請步驟

1. 到 <https://www.importyeti.com> 註冊(免費,email 即可)
2. **不碰 API**——這是**純手動來源**:網頁搜尋 → 眼睛挑線索 → 手抄成 CSV → 匯入系統。
   系統這端只負責接收你整理好的 CSV(欄位認得 Consignee),不做任何自動抓取

## 三種搜尋玩法(按價值排序)

### 玩法 A:搜競品公司,看他們的美國物流鏈

搜尋框輸入 `Planetary Design`、`Fellow Industries` 等競品名 →
看該公司頁面的 shipment 紀錄與關聯公司。重點看:

- **Consignee(收貨方)**:誰在收這些貨——若不是品牌自己,就是經銷商/大客戶
- 出貨頻率與櫃量:判斷通路規模

### 玩法 B:搜產品關鍵字,找品類進口商

搜 `vacuum container`、`food storage container`、`canister`、`kitchenware` →
列出進口這類產品的美國公司。這些公司**已在進口同品類**,
換供應商或加產品線的門檻最低。

### 玩法 C:搜台灣供應商,找「習慣跟台灣做生意」的買家

以 Supplier 國家/名稱切入(台灣的家居用品代工廠)→ 看美國端的 consignee。
這群公司對台灣製造的信任成本為零。

## 整理成 CSV → 匯入系統

ImportYeti 頁面上把目標公司抄下來(免費版以手抄/複製為主),整理成 CSV。
**系統認得提單術語欄位**,以下欄位名都會自動映射:

```csv
Consignee,City,State
ABC Kitchen Distributors,Chicago,IL
XYZ Home Goods Import,Elk Grove Village,IL
```

- `Consignee` / `Consignee Name` / `Company` → 公司名(擇一即可)
- `City` / `State` → 城市 / 州(全名或縮寫都可)
- 沒有聯絡人與 email 是正常的(提單只有公司)——匯入後用
  Apollo / Hunter Domain Search 補決策人,同 Places 的兩段式流程

匯入方式(擇一):

- **Web UI(建議)**:匯入頁 → CSV 上傳 → **來源標籤選「ImportYeti 海關」、
  Tier 選 T0 Rep Group** → 匯入
- CLI:`buyer-intel ingest --source manual --file imports/importyeti.csv --tier T0_rep`

> **為什麼 Tier 選 T0**:進口商/經銷商屬於戰略的 T0 層(中間人),
> 走獨立信件通道(談經銷合作,不談批發採購),不套零售商評分。

## 與其他來源的分工

| 來源 | 給的訊號 | 找誰 |
|---|---|---|
| Apollo | 這個「人」是採購決策者 | T1/T2 買手 |
| Google 地圖 | 這家「店」真實存在 | T1 獨立店 |
| 競品 Stockists | 這家店「已在賣同品類」 | T1/T2 零售商 |
| **ImportYeti** | **這家公司「會進口」** | **T0 經銷商** |

## 疑難排解與注意事項

| 情況 | 說明 |
|---|---|
| 搜不到某競品 | 走空運或內貿的看不到(海關提單只涵蓋海運);換產品關鍵字切入 |
| 資料有延遲 | 提單公開有數週到數月延遲,正常;找的是「有進口習慣」的公司,不是即時動態 |
| Consignee 顯示為報關行/貨代 | 部分公司用貨代名義收貨;跳過 freight forwarder / logistics 字樣的公司 |
| 同公司多筆 shipment | 只需要公司名一筆,系統匯入會自動去重 |
| 免費版限制 | 每月搜尋/查看次數有上限;驗證期夠用,超過再評估付費(仍遠低於 Panjiva) |
