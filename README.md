# Buyer Intelligence System

支援 **The Inspired Home Show 2027(芝加哥,3/9–3/11)** 參展的全週期 B2B 買家開發系統:
**展前找買家 → 展中管理接觸 → 展後自動跟進**。

本 repo 是 [`plan/buyer_intelligence_architecture.html`](plan/buyer_intelligence_architecture.html) 的實作;
商業脈絡見 [`plan/ankomn_strategy_report.html`](plan/ankomn_strategy_report.html)。

## 系統架構(五層 Pipeline)

| 層 | 模組 | 功能 | 模型 |
|---|---|---|---|
| L1 資料擷取 | `adapters/` | Apollo / Google Places / IHA 名錄 / CSV 匯入,統一產出 `RawLead` | — |
| L2 清洗豐富 | `enrich.py` | 去重(rapidfuzz 模糊比對 + email domain)、Hunter 驗證、web 搜尋背景補全 | Sonnet 搜尋 + Haiku 抽取 |
| L3 評分分級 | `scoring.py` | 規則基礎分 + LLM 契合度判斷,加權後分 A/B/C;C 級自動歸檔 | Sonnet |
| L4 觸達引擎 | `outreach.py` | 個人化信件生成 → Opus 扮演美國 buyer 批判 → 重寫迴圈(≤3 輪)→ 人工覆核 | Sonnet 寫 + Opus 審 |
| L5 展中作戰 | `field_ops/`、`webui/` | 名片 OCR、即時 company brief、same-day follow-up、pipeline 看板 | Haiku + Sonnet |
| 操作介面 | `webui/`(主)、`cli.py` | 全功能 Web UI:名單/覆核改稿/一鍵寄信/追蹤/匯入/背景 pipeline | — |

流程編排使用 **LangGraph**(`graph.py`):條件邊依評分分流、critique 迴圈退回重寫、
SQLite checkpoint 讓批次中斷後可續跑不重花 API 費用。

```mermaid
graph TD;
    S([開始]) --> enrich["enrich<br/>L2 背景豐富<br/>(Sonnet+WebSearch → Haiku 抽取)"];
    enrich --> score["score<br/>L3 混合評分<br/>(規則 + Sonnet 判斷)"];
    score -. "A / B 級(≥50)" .-> draft["draft<br/>L4 個人化信件生成<br/>(Sonnet)"];
    score -. "C 級(<50)歸檔" .-> E([結束]);
    draft --> critique["critique<br/>Opus 扮演美國 buyer 審稿"];
    critique -. "revise(上限 3 輪)" .-> draft;
    critique -. "pass" .-> review["review<br/>人工覆核佇列<br/>(pending_draft 入庫)"];
    review --> E;
```

> 每筆 lead 以 `thread_id=lead-{id}` 執行本圖;每個節點結束即寫回
> `leads.db`,checkpoint 存於 `checkpoints.db`,中斷後續跑不重花費用。
> T0 Rep 在 score 節點直接標 A 級走信件通道(獨立於零售商評分)。

### 各層職責詳解

#### L1 資料擷取 — 進料口
**負責:把散落各處的潛在買家變成統一格式的原始名單(`RawLead`)。**

- 輸入:Apollo 搜尋條件 / Places 城市查詢 / IHA 名錄 CSV / LinkedIn 匯出 CSV
- 輸出:`RawLead`(公司、聯絡人、職稱、email、地區、通路分層)
- 指令:`buyer-intel ingest --source apollo|places|iha|manual`
- 四個來源各有分工:Apollo 找「人」(決策人+email)、Places 找「店」
  (獨立小商家,B2B 資料庫查不到的)、IHA 名錄是 T0 Rep 線索的核心來源、
  manual 承接一切手動匯出與展中名片
- 沒有它:巧婦難為無米之炊。**目前的專案瓶頸就在這層**(Apollo API 需付費方案)

#### L2 清洗豐富 — 情報引擎(最花時間與額度的一層)
**負責:把「一行公司名」變成「可以拿來寫信的事實」。**

- 輸入:`RawLead` → 輸出:補全後的 `Lead`(門市數、通路類型、是否賣競品、背景摘要)
- 三個動作依序:
  1. **去重**:公司名模糊比對(自動剝除 Inc/LLC 等後綴)+ email 網域合併,
     資訊較完整的一筆勝出——避免同一家被觸達兩次
  2. **email 驗證**(Hunter):無效信箱標記,保護寄件網域信譽——
     **在 pipeline 背景執行,不在匯入時**(匯入保持秒進不卡 UI);
     手動改信箱或切換主收件人時則當場即時驗證單筆
  3. **背景豐富**:Sonnet 帶 WebSearch 上網查這家公司(規模、通路、競品),
     Haiku 把查到的內容抽成結構化欄位
- 沒有它:L3 沒依據亂評分,L4 的「為什麼找上你」只能瞎編——**個人化立刻退化成罐頭信**

#### L3 評分分級 — 資源守門員
**負責:決定誰值得花觸達成本,誰直接歸檔。**

- 輸入:補全後的 `Lead` → 輸出:分數(0–100)、分級(A/B/C)、可解釋的評分依據
- 混合式評分:規則算得準的用規則(規模、地區、職稱,零成本可解釋),
  需要判斷的交給 LLM(通路契合度)——四維加權 40/25/20/15
- 條件分流:**A(≥70)/ B(50–69)進 L4 寫信;C(<50)自動歸檔,一毛不花**
- T0 Rep 例外:不套零售商評分,直接進信件通道
- 沒有它:額度與人工平均撒在爛 lead 上,好 lead 反而沒被優先對待

#### L4 觸達引擎 — 真正的產品出口
**負責:把 L2 的事實變成一封「值得美國 buyer 回覆」的信。**

- 輸入:A/B 級 `Lead` + 背景事實 → 輸出:通過審稿的信件草稿(進人工覆核佇列)
- 內建品管迴圈:Sonnet 寫 → **Opus 扮演「一天收幾十封開發信的美國買家」毒舌審稿**
  (超過 150 字?像罐頭?理由瞎編?→ 退回重寫,上限 3 輪)
- 信件鐵則:一句話價值主張、為何找上「這一家」的具體理由(只准用 L2 查到的
  事實)、攤位資訊 + Calendly 連結
- **絕不自動寄送**:過稿只是進佇列,`buyer-intel review` 人工核准後輸出
  `outbox/`,由人寄出
- 沒有它:前面三層的投資全部到不了買家眼前

#### L5 展中/展後作戰 — 收割紀律
**負責:展會三天的現場執行力,以及之後的 pipeline 追蹤。**

- **展中**(`buyer-intel serve`,手機開網頁):拍名片 → OCR 入庫 → 自動比對
  「預約客戶還是新接觸」→ 秒回 company brief(什麼通路、該談 wholesale 還是
  rep、FOB/DDP 建議、客製開場白)→ 談完當場記會談重點
- **每晚**(`buyer-intel followup`):依當日會談紀錄批次生成 same-day follow-up
  草稿——支撐「24 小時跟進率 100%」這條 KPI
- **展後**(`buyer-intel dashboard`):漏斗看板 + 逾期未跟進警示,
  追蹤每筆 lead 從接觸到 PO 的階段
- 沒有它:名片變成回國後的一疊廢紙,展會投資的轉化率腰斬

#### 一句話定位

> **L2 是心臟(生產事實),L4 是出口(事實變會議),L3 省錢,L5 收割;
> 但專案生死在系統外的兩件事——L1 的名單量,和寄信網域的到達率。**

### 模型分工(已更新為現行模型 ID)

| 任務 | 模型 ID | 理由 |
|---|---|---|
| 清洗、抽取、名片 OCR 結構化 | `claude-haiku-4-5` | 高頻低難度、成本敏感 |
| 背景豐富、契合度判斷、信件/brief 生成 | `claude-sonnet-5` | 需推理與 web search 綜整,性價比最佳 |
| 信件批判審稿(扮演美國 buyer) | `claude-opus-4-8` | 低頻高價值;一封爛信毀掉一個 A 級 lead |

### 兩條鐵律

1. **Human-in-the-loop**:系統只產草稿,**絕不直接寄信**。核准後的信件輸出到
   `outbox/`,由人工用郵件軟體寄出 —— B2B 信任是資產,不容 AI 幻覺揮霍。
2. **Rep Group(T0)走獨立通道**:不套零售商評分模型,直接進觸達並以
   代理合作(而非批發採購)角度撰寫信件。

## 安裝

```bash
cd buyer-intelligence
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # 依需求填入金鑰(見下)
```

### 每次使用前:啟用虛擬環境(activate)

`buyer-intel` 指令裝在專案的虛擬環境(`.venv`,隱藏資料夾)裡,
**每次開新的終端機視窗都要先啟用**,否則會出現 `command not found: buyer-intel`:

```bash
cd ~/ankomn/buyer-intelligence
source .venv/bin/activate      # 啟用後提示字元會出現 (buyer-intelligence) 前綴
buyer-intel serve              # 之後指令直接打即可
```

- 離開:`deactivate`(或直接關閉終端機視窗)
- **不想每次啟用**的替代寫法:用完整路徑直接執行
  `.venv/bin/buyer-intel serve`(效果相同)
- 注意:指令要在**專案根目錄**跑(`~/ankomn/buyer-intelligence`),
  不要進到 `src/` 裡面

**LLM 後端二選一**(`LLM_BACKEND` 環境變數,見 `llm.py`):

| 後端 | 計費 | 需求 | 取捨 |
|---|---|---|---|
| `claude_code`(預設) | **Claude 訂閱額度** | 已安裝並登入 Claude Code(`claude` 指令) | 免 API key 免儲值;受訂閱用量上限、單筆較慢、結構化輸出以 JSON 解析實作 |
| `api` | API 額度(platform.claude.com 儲值) | `ant auth login` 或 `.env` 填 `ANTHROPIC_API_KEY` | 原生結構化輸出 / web_search / vision,品質與速度最佳 |

訂閱方案沒有 Opus 時,設 `CLI_MODEL_TOP=sonnet` 把審稿模型降級。
Apollo / Hunter / Google Maps 未設定時,對應 adapter 會明確報錯,
其餘功能照常運作(email 驗證會標為未驗證)。

**三個外部工具的申請步驟、方案額度與疑難排解,見 [docs/](docs/) 資料夾**:
[Apollo](docs/apollo.md)、[Hunter](docs/hunter.md)、[Google Maps](docs/google-maps.md)。

## 使用方式 A:Web UI(建議,唯一要打的指令)

```bash
buyer-intel serve        # 然後瀏覽器開 http://localhost:8000
```

所有操作都在網頁完成:

- **儀表板**:漏斗+逾期警示+背景任務狀態;底部「危險區」可**清空全部資料**
  (需輸入 `DELETE` 雙重確認,任務執行中會鎖住)
- **名單**:階段/分級篩選、搜尋;列表顯示**州**與**來源**欄;詳情頁含
  全部聯絡人表(同公司**全部保留**,一鍵切換主收件人)、**LinkedIn 在職查核**
  連結(寄信前 30 秒人工確認)、**單筆刪除**(跳確認框)
- **覆核佇列**:線上改稿+核准+一鍵開郵件寄信;每封附 LinkedIn 查核連結
- **匯入名單**:① CSV 上傳(**自選來源標籤**:Apollo/LinkedIn/Stockists/IHA…,
  秒進不卡)② **Google 地圖掃描**(自訂搜尋句「業態 in 城市, 州」)
- **Pipeline**:可依**州 × 來源**篩選(下拉附各項筆數),背景執行+即時日誌
- **推進階段**:回信/會議/樣品/報價/PO 按鈕
- **名片掃描**:手機開 `http://<電腦IP>:8000/card`

開發信一律全英文(三重語言防線);email 驗證在 pipeline 背景執行。

## 使用方式 B:CLI(腳本化用,功能相同)

```bash
# 0. 建庫
buyer-intel init

# 1. L1 擷取名單(四選一或混用)
buyer-intel ingest --source manual --file examples/seed_leads.csv          # CSV / LinkedIn 匯出
buyer-intel ingest --source apollo --query "specialty coffee retailer"     # Apollo 決策人搜尋
buyer-intel ingest --source places --query "coffee roaster in Austin, TX"  # 掃城市店家
buyer-intel ingest --source iha --file iha_exhibitors.csv --tier T0_rep    # IHA 名錄(Rep 線索)

# 2. L2–L4 全流程:豐富 → 評分 → 信件草稿 → 覆核佇列
buyer-intel pipeline --limit 20 --state IL --source apollo  # 州/來源/筆數皆可篩

# 3. 人工覆核:逐筆看草稿,核准後輸出 outbox/ 由人工寄送
buyer-intel review

# 4. 看板:漏斗視圖 + 逾期未跟進警示
buyer-intel dashboard && open dashboard.html
```

### 展中模式(2027/3/9–11)

```bash
buyer-intel serve            # 手機連同一 Wi-Fi,開 http://<電腦IP>:8000
```

白天:拍名片 → OCR 入庫 → 自動比對「預約客戶 or 新接觸」→ 秒回 company brief
(通路類型、該談 wholesale 還是 rep、FOB/DDP 建議、客製開場白)→ 談完當場記會談重點。

每晚:

```bash
buyer-intel followup         # 依當日會談紀錄生成 same-day follow-up 草稿
buyer-intel review           # 人工掃過後寄出 —— 24 小時內跟進率 100% 是 KPI
```

## 名單來源指南(L1 Enhancement)

系統內建四來源之外,依含金量排序的擴充來源——共同入口都是
**CSV → Web UI 匯入頁**(`manual` adapter 自動識別欄位):

| # | 來源 | 找什麼 | 成本 | 含金量 / 備註 |
|---|---|---|---|---|
| 1 | **競品 Stockists 頁**(Fellow、Planetary Design 官網「Where to Buy」) | 已在賣真空保鮮罐的零售店 | 免費 | **最高**——品類驗證完成的「預審合格」名單,評分自動因 `sells_competitors` 加分;手動整理一個下午可收上百家 |
| 2 | **SCA 生態**(Specialty Coffee Association 會員名錄、SCA Expo 展商名單) | 精品咖啡通路核心玩家 | 免費查詢 | 展商名單通常公開;每年 4 月 Expo |
| 3 | **LinkedIn Sales Navigator** | 決策人(與 Apollo 互補) | ~$99/月,有免費試用 | Buyer/Owner × 零售 × 地區搜尋 → 匯出 CSV |
| 4 | **Faire 批發市場** | 活躍批發買家 | 免費瀏覽 | **雙重身份**:名單來源 + 本身就是可上架的通路(值得戰略評估) |
| 5 | **Hunter Domain Search**(已有帳號) | 給網域 → 列出公司 email | 免費額度 | 與 Places 完美互補:Places 掃店名網站 → Hunter 補聯絡人,免費鏈路部分替代 Apollo 付費牆 |
| 6 | **咖啡產業媒體**(Sprudge、Daily Coffee News) | 新開業/擴店的成長型烘豆商 | 免費 | 信件可引用新聞事實,個人化拉滿 |
| 7 | Apollo 替代品(RocketReach / Lusha / Clay) | 決策人+email | 與 Apollo 相近 | 需要時再評估;ZoomInfo 企業級太貴 |

**建議的免費組合鏈**:競品 Stockists + Places 掃 P1 城市 + Hunter Domain Search
補 email,與 Apollo 匯出並行。每筆名單的 `source` 欄位會記錄來源,
數週後對比各來源回覆率,讓數據決定加碼哪一條。

不建議:進口海關數據(Panjiva,精準但貴)、州政府商業登記(太原始)、
Instagram 標籤挖掘(效率太低)。

### Apollo vs LinkedIn Sales Navigator(2026 年查證的結論)

一句話:**Apollo 是名單工廠(找人+給 email+可匯出 CSV),Sales Navigator 是
找人雷達(資料最新但不給 email、不能匯出)**——本系統進料是 CSV,所以主力
必然是 Apollo。

| | Apollo Basic | Sales Navigator Core |
|---|---|---|
| 年繳月費 | **$49** | $89.99 |
| 給 email / 匯出 CSV | ✅ / ✅ | ❌ / ❌(第三方爬蟲工具有封號風險) |
| 資料新鮮度 | ~90%,小公司較弱(Hunter 驗證層可擋過期信箱) | 最強(本人自維護,換工作即時) |
| 計量 | 統一 credit 池,**月底清零不累積**——升級後每月用滿 | 50 InMail/月 |

執行策略:

1. **主力 Apollo**:免費期網頁匯出;量產期升 Basic($49)
2. **小型獨立店兩者都弱**(老闆不維護 LinkedIn):走 Places + Hunter
   Domain Search 免費鏈,不是這兩家的戰場
3. **Sales Nav 只當精準補刀**:展前打 T2 大零售品類買手時(這種人 LinkedIn
   資料最準)開 **30 天免費試用**,配 Apollo Chrome 擴充在 LinkedIn 頁面
   揭露 email → 手動 CSV 匯入;試用完即停
4. **兩個都長期訂閱是浪費**

## 評分模型(L3)

| 維度 | 權重 | 邏輯 |
|---|---|---|
| 通路契合度 | 40% | 咖啡器材通路 > 廚房專賣 > 一般零售;已賣競品(Fellow Atmos 等)加分。LLM 判斷 |
| 規模適配度 | 25% | 甜蜜點 5–100 家門市;**過大反而扣分**。規則計算 |
| 地區優先序 | 20% | PNW、TX(P1)> CA、NY(P2)> 中西部(P3)。規則計算 |
| 決策權 | 15% | Owner / Buyer / Category Manager 高分。規則 + LLM 各半 |

總分 ≥70 → **A**(進觸達,信件人工逐封覆核);50–69 → **B**(批次處理);
<50 → **C**(歸檔不觸達)。每筆都有 `score_rationale` 可解釋欄位供人工抽查。

權重與門檻都在 [`config.py`](src/buyer_intel/config.py) —— 展後應以實際回覆率回頭校準。

## 目錄結構

```
buyer-intelligence/
├── src/buyer_intel/
│   ├── config.py            # 模型分工、評分權重、路徑(單一事實來源)
│   ├── models.py            # Pydantic:Lead / RawLead / Interaction 等
│   ├── db.py                # SQLite 單檔存取(data/leads.db)
│   ├── llm.py               # Anthropic client 共用工具(含 pause_turn 處理)
│   ├── adapters/            # L1:apollo / places / iha / manual
│   ├── enrich.py            # L2:去重、email 驗證、web 搜尋豐富
│   ├── scoring.py           # L3:混合式評分與分級
│   ├── outreach.py          # L4:draft → critique → rewrite + follow-up
│   ├── graph.py             # LangGraph 編排 + SQLite checkpoint + prepare_batch
│   ├── actions.py           # 共用業務動作(核准/退回/寄信/track,CLI 與 UI 共用)
│   ├── webui/               # ★ Web UI:app.py(全部頁面)+ jobs.py(背景任務)
│   ├── field_ops/           # L5:ocr.py(名片)/ brief.py(攻略)
│   ├── dashboard.py         # 靜態看板 HTML(Web UI 首頁為即時版)
│   └── cli.py               # buyer-intel 指令入口(serve 啟動 Web UI)
├── plan/                    # 兩份正式文件:最終計劃書 + 系統規格書(HTML)
├── docs/                    # 外部工具指南(Apollo / Hunter / Google Maps)
├── tests/                   # 規則評分與去重的單元測試(不需 API 金鑰)
├── examples/seed_leads.csv  # 種子名單範例(T1 咖啡器材電商)
├── imports/                 # 匯入的名單 CSV(git 忽略,含個資)
├── outbox/                  # 核准信件 .eml(git 忽略)
├── PROGRESS.md              # 專案日誌(新條目往上加)
└── data/                    # leads.db / checkpoints.db(git 忽略)
```

## 測試

```bash
pytest        # 純規則邏輯,不呼叫 API、不需金鑰
```

## 營運成本

對應架構報告第 07 節:`claude_code` 後端下 LLM 呼叫**走訂閱額度,不另計費**
(但受訂閱用量上限限制);`api` 後端約 $30–80/月(名單開發高峰期偏上緣)。
加上 Apollo / Hunter / Calendly 合計約 **$0–240/月**。`pipeline --limit` 可控制單次批量。

## 開發里程碑對照

| 里程碑 | 時間 | 本 repo 對應 |
|---|---|---|
| M1 資料骨幹 | 2026-08 | `models.py`、`db.py`、apollo/places adapter ✅ |
| M2 評分分級 | 2026-09 | `enrich.py`、`scoring.py`、iha adapter ✅ |
| M3 觸達引擎 | 2026-11 | `outreach.py`、`graph.py`、`review` 指令 ✅ |
| M4 展中模組 | 2027-01 | `field_ops/`(OCR、brief、手機 UI)✅ |
| M5 Pipeline 看板 | 2027-02 | `dashboard.py`、`followup` 指令 ✅ |

> 本 repo 為 v0.1 骨架:五層全部可執行,但 Apollo / IHA 名錄的欄位映射需依
> 實際帳號與檔案格式微調(見各 adapter 註解)。

## 待辦清單

### 短期:驗證期(現在 → 2026-08,對應 M1)

- [x] 首批真實名單(Apollo 匯出 25 人 → 18 家)完成匯入、評分與信件生成;
      評分模型驗證通過(P1/芝加哥烘豆商 A、通路不合者 C、T3 被攔)
- [ ] Web UI 覆核佇列逐封核准 → 一鍵開郵件寄出第一批開發信
- [x] 申請 Apollo 帳號 → 已填 `APOLLO_API_KEY`(注意:**People Search API 需付費方案**,
      免費方案改走網頁搜尋 → 匯出 CSV → Web UI 匯入頁上傳)
- [x] 申請 Hunter 帳號 → 已填 `HUNTER_API_KEY`(Free 方案 100 次驗證/月;
      驗證於 pipeline 背景執行)
- [x] 申請 Google Maps 金鑰 → 已填 `GOOGLE_MAPS_API_KEY`(Places 掃描實測可用,
      匯入頁可自訂搜尋句)
- [ ] 建 Calendly 活動(「TIHS 攤位會議 30 分鐘」)→ 填 `CALENDLY_URL`
- [ ] **整理競品 Stockists 名單**(Fellow / Planetary Design 官網「Where to Buy」
      → CSV → 匯入):最高含金量的免費來源,見「名單來源指南」

### 中期:名單開發期(2026-09 → 12,對應 M2–M3)

- [ ] **寄信基礎設施(L4 成敗的最大實務風險)**:購買專用寄信網域(勿用主網域)、
      設定 SPF/DKIM/DMARC、網域暖機 4–6 週後才開始正式 outreach
- [ ] **CAN-SPAM 合規**:信尾加公司實體地址與退訂機制
- [ ] 開信/回覆追蹤:量大時評估 Instantly / Smartlead 等寄送服務 API
- [ ] Apollo 升級付費方案(免費 tier 對 email export 有限制)
- [ ] IHA 名錄取得後接入 `iha` adapter,建立 T0 Rep Group 名單
- [ ] **名單量反推**:冷信會議轉化率約 2–5%,展前要敲定 15–20 場會議
      → A 級名單需 400–800 筆,以此訂每月名單開發目標

### 展前/展後(2027-01 →,對應 M4–M5)

- [ ] 展中模組實機演練:拿台灣名片測 `buyer-intel serve` 的 OCR 與 brief
- [ ] 評分權重校準:用實際回覆率/會議轉化率回頭調 `config.SCORE_WEIGHTS`
- [ ] 展後把 PO 轉化數據回填,驗證戰略報告 KPI(首批訂單 3–5 家)

### 戰略層(系統外,建議補進戰略報告)

- [ ] 單位經濟:批發價、毛利結構、MOQ、ROI 門檻
- [ ] MAP(最低廣告價格)政策:預防 T1 電商 × T2 零售 × 自有 DTC 的通路衝突
- [ ] gia 獎項報名死線確認(通常在展前數月截止)
