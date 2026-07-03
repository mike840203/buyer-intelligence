# Ankomn Buyer Intelligence System

支援 **The Inspired Home Show 2027(芝加哥,3/9–3/11)** 參展的全週期 B2B 買家開發系統:
**展前找買家 → 展中管理接觸 → 展後自動跟進**。

本 repo 是 [`plan/buyer_intelligence_architecture.html`](../plan/buyer_intelligence_architecture.html) 的實作;
商業脈絡見 [`plan/ankomn_strategy_report.html`](../plan/ankomn_strategy_report.html)。

## 系統架構(五層 Pipeline)

| 層 | 模組 | 功能 | 模型 |
|---|---|---|---|
| L1 資料擷取 | `adapters/` | Apollo / Google Places / IHA 名錄 / CSV 匯入,統一產出 `RawLead` | — |
| L2 清洗豐富 | `enrich.py` | 去重(rapidfuzz 模糊比對 + email domain)、Hunter 驗證、web 搜尋背景補全 | Sonnet 搜尋 + Haiku 抽取 |
| L3 評分分級 | `scoring.py` | 規則基礎分 + LLM 契合度判斷,加權後分 A/B/C;C 級自動歸檔 | Sonnet |
| L4 觸達引擎 | `outreach.py` | 個人化信件生成 → Opus 扮演美國 buyer 批判 → 重寫迴圈(≤3 輪)→ 人工覆核 | Sonnet 寫 + Opus 審 |
| L5 展中作戰 | `field_ops/`、`dashboard.py` | 名片 OCR、即時 company brief、same-day follow-up、pipeline 看板 | Haiku + Sonnet |

流程編排使用 **LangGraph**(`graph.py`):條件邊依評分分流、critique 迴圈退回重寫、
SQLite checkpoint 讓批次中斷後可續跑不重花 API 費用。

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

**LLM 後端二選一**(`LLM_BACKEND` 環境變數,見 `llm.py`):

| 後端 | 計費 | 需求 | 取捨 |
|---|---|---|---|
| `claude_code`(預設) | **Claude 訂閱額度** | 已安裝並登入 Claude Code(`claude` 指令) | 免 API key 免儲值;受訂閱用量上限、單筆較慢、結構化輸出以 JSON 解析實作 |
| `api` | API 額度(platform.claude.com 儲值) | `ant auth login` 或 `.env` 填 `ANTHROPIC_API_KEY` | 原生結構化輸出 / web_search / vision,品質與速度最佳 |

訂閱方案沒有 Opus 時,設 `CLI_MODEL_TOP=sonnet` 把審稿模型降級。
Apollo / Hunter / Google Maps 未設定時,對應 adapter 會明確報錯,
其餘功能照常運作(email 驗證會標為未驗證)。

## 使用流程

```bash
# 0. 建庫
buyer-intel init

# 1. L1 擷取名單(四選一或混用)
buyer-intel ingest --source manual --file examples/seed_leads.csv          # CSV / LinkedIn 匯出
buyer-intel ingest --source apollo --query "specialty coffee retailer"     # Apollo 決策人搜尋
buyer-intel ingest --source places --query "coffee roaster in Austin, TX"  # 掃城市店家
buyer-intel ingest --source iha --file iha_exhibitors.csv --tier T0_rep    # IHA 名錄(Rep 線索)

# 2. L2–L4 全流程:豐富 → 評分 → 信件草稿 → 覆核佇列
buyer-intel pipeline --limit 20        # --limit 控制單次 API 花費

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
│   ├── graph.py             # LangGraph 編排 + SQLite checkpoint
│   ├── field_ops/           # L5:ocr.py / brief.py / app.py(FastAPI)
│   ├── dashboard.py         # L5:pipeline 看板 HTML
│   └── cli.py               # buyer-intel 指令入口
├── tests/                   # 規則評分與去重的單元測試(不需 API 金鑰)
├── examples/seed_leads.csv  # 種子名單範例(T1 咖啡器材電商)
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

## 已知限制與待辦

- **寄信基礎設施**:冷開發信的網域信譽(SPF/DKIM/DMARC、網域暖機)與
  CAN-SPAM 合規(實體地址、退訂連結)不在本系統範圍,寄送前必須先處理 ——
  這是 L4 成敗的最大實務風險。
- **開信/回覆追蹤**:目前以人工寄送為主,未整合 email 追蹤;若量大可評估
  接 Instantly / Smartlead 等寄送服務的 API。
- **評分回饋迴路**:權重目前是先驗設定,展後應以實際會議轉化率校準。
- Apollo 免費 tier 對 email export 有限制,量產期(M2)需升級付費方案。
