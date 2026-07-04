# 專案日誌 — Buyer Intelligence System

> 格式:新條目加在最上面。記錄「做了什麼、決策與理由、卡在哪、下一步」。

---

## 2026-07-05 收件人自主權 + 英文信三重防線

1. **去重改為「全部保留」**:同公司多聯絡人結構化存 `alt_contacts`
   (姓名/職稱/email 完整),UI 詳情頁列全表、「設為主收件人」一鍵切換
   (原主退為備選,換人自動重置驗證旗標)。演算法只給預設,寄給誰由人判斷。
   注意:既有名單的備援還在 notes 文字裡;重新匯入 CSV 即得結構化版本。
2. **英文信混入日文的根因與修復**:`claude -p` 子程序會載入使用者全域
   CLAUDE.md「一律繁中回覆」,與寫英文信指令衝突 → CJK 混入。
   三重防線:`--append-system-prompt` 語言覆寫 + 生成後 CJK 正則偵測重寫
   (含日文假名)+ Opus 審稿把非英文列 instant fail。
3. 測試 15 → 18;規格書、README 同步更新。

---

## 2026-07-04(四)全面 Web UI 化 + 平行加速 + 最終文件

1. **Web UI(`src/buyer_intel/webui/`,新資料夾)**:日常操作不再需要 CLI,
   `buyer-intel serve` 後全部在 http://localhost:8000 —— 儀表板/名單/覆核
   (可線上改稿)/mailto 一鍵寄信/track 按鈕/CSV 匯入/pipeline 背景執行含
   即時日誌/名片掃描(手機 /card)。九條路由煙霧測試全過。
2. **共用層重構**:業務動作抽到 `actions.py`、批次準備抽到
   `graph.prepare_batch()`——CLI 與 UI 共用同一份邏輯。
3. **pipeline 平行化**:`--workers`(預設 3)約快 3 倍;單筆失敗不拖垮整批;
   SQLite 加 timeout 防鎖。慢的根因是 L2 真的上網查公司(1–3 分/筆)+
   claude_code 後端程序冷啟動,是 $0 成本的代價;量產期可切 LLM_BACKEND=api。
4. **去重演算法兩次進化**(使用者真實 Apollo 資料驅動):免費信箱不當同公司
   證據(gmail 誤殺 bug);同公司多聯絡人依「品類買手 > Owner > 泛買手」
   保留,落選者轉備援聯絡人存 notes;訊息顯示保留者職稱與判定依據。
5. **最終文件**(plan/):`ankomn_strategy_report_final.html`(給老闆簽核:
   名單數學、預算 $20–35K、單位經濟、MAP、決策請求、六條驗算)+
   `buyer_intelligence_architecture_v2.html`(as-built 規格書,可據此重建)。
6. 測試 17/17;實測結果:芝加哥三家精品烘豆商全 A、PersonalizationMall C、
   Costco 被 T3 防線攔截。

---

## 2026-07-04(三)戰略防線修復 + 全名單首輪完成

### 修復(計畫比對發現的偏差)

1. **T3 大型量販「不主動觸達」防線**(🔴 違反戰略,已修):
   `scoring.archive_t3()` 在任何 LLM 呼叫前攔截歸檔;`cli` pipeline 入口
   再攔一次連 L2 成本都不花。實測 Costco 被 `⛔` 秒攔,零額度。
   L5 展中被動接觸不受影響(符合戰略「交由 Rep 評估」)。
2. **pipeline 不再重跑待覆核 lead**:有 `pending_draft` 者跳過並提示。
3. **checkpoint 警告根治**:graph state 改存原生 dict(節點內重建 Pydantic),
   舊 `checkpoints.db` 已清除重建。實測新跑無任何警告。
4. 測試 8 → 10(新增 T3 防線、T0 獨立通道),全過。

### 首輪名單結果(評分模型驗證 ✅ 符合戰略預期)

| 公司 | 地區 | 分級 | 分數 | 狀態 |
|---|---|---|---|---|
| Seattle Coffee Gear | PNW(P1) | A | 77.5 | 已核准 |
| Clive Coffee | PNW(P1) | A | 74.7 | 已核准 |
| Visions Espresso | PNW(P1) | A | 73.4 | 待覆核 |
| Whole Latte Love | NY(P2) | A | 71.1 | 已核准 |
| Prima Coffee Equipment | KY(OTHER) | B | 62.7 | 已核准 |
| Costco Wholesale | T3 | — | — | ⛔ 依戰略歸檔 |

P1 地區全 A、OTHER 地區 B、T3 被攔——權重設計與戰略地區優先序一致。

### ⚠️ 當前瓶頸(明確)

outbox/ 四封核准信**全部沒有收件人**——種子名單只有公司沒有聯絡人。
下一步唯一要事:Apollo 網頁搜尋(Owner/Buyer 職稱 × WA/OR/TX × 員工 ≤200)
匯出含 email 的名單。已決策:驗證期用手動搜尋(篩選器更多、可先用
商業判斷粗篩、員工數上限天然執行 T3 防線),量產期再評估升級 API。

---

## 2026-07-04(補記)計畫 vs 現實 事實核對

兩份規劃文件(`plan/`)仍然有效,大方向不變;架構報告有三處事實更新:

1. **執行後端**:原計畫用 Claude API($30–80/月)→ 實際改走 Claude Code CLI
   訂閱額度(`LLM_BACKEND=claude_code`),LLM 增量成本 $0,受訂閱用量上限;
   `api` 後端保留,儲值後可隨時切回。
2. **Apollo 免費假設不成立**:People Search API 需付費方案(403 實測,
   換新金鑰再測仍同——是方案限制非金鑰問題)。驗證期改走網頁搜尋 →
   匯出 CSV → `manual` ingest。
   例外:`organizations/enrich`(公司補全)**免費方案可用**(200 實測),
   可作為 L2 的免費結構化補全來源(尚未接入,選項保留)。
3. **模型 ID 落實**:Haiku/Sonnet/Opus → `claude-haiku-4-5` / `claude-sonnet-5`
   / `claude-opus-4-8`。

戰略報告零偏差,目前處於「基礎建設期(7–8 月)」且略超前:
M1–M5 程式骨架已一次建完,剩資料量產與實戰調校。

---

## 2026-07-04

### 完成事項

**規劃與評估**
- 評估兩份規劃報告(`plan/` 下的戰略報告與架構報告),識別缺口:
  戰略面缺單位經濟、MAP 定價政策、gia 報名死線、名單量反推;
  技術面缺寄信基礎設施(SPF/DKIM/DMARC、網域暖機)與評分回饋迴路。

**Repo 建置(commit `9f12e5b`)**
- 依架構報告建立五層 pipeline 完整骨架:L1 四個 adapter(Apollo / Places /
  IHA / Manual)、L2 清洗豐富、L3 混合評分、L4 draft→critique 迴圈、
  L5 展中作戰(OCR / brief / 手機 UI / 看板),LangGraph 編排 + SQLite checkpoint。
- 單元測試 8/8 通過;修正一個真實 bug(公司名法律後綴導致去重失效)。

**LLM 執行方式決策(commit `b265eea` / v1)**
- 情境:無 API 儲值,決定走 Claude 訂閱額度。
- `llm.py` 重構為雙後端抽象:`claude_code`(預設,`claude -p` headless 走訂閱)
  / `api`(SDK 直連,留待未來切換)。
- `ant auth login` 已完成(profile: default);claude CLI 以 symlink 接通
  (`~/.local/bin/claude` → VSCode 擴充 2.1.199 內建 binary)。

**端對端驗證(訂閱額度,全數成功)**
- Prima Coffee Equipment 單筆全流程:WebSearch 查到真實背景(年營收約 $5M、
  已賣 Fellow Atmos / Planetary Design 競品)→ 評分 60.3 / B 級(地區 KY 與
  職稱未知拉低,判斷合理)→ 信件草稿通過 Opus 審稿 → 已入人工覆核佇列。
- 資料庫現況:4 筆種子名單入庫,1 筆已處理(B),3 筆 stage=new 待跑。

**文件(commit `b577fdd`)**
- README 新增四階段待辦清單(驗證期 / 名單開發期 / 展前展後 / 戰略層)。
- 看板已可產出:`buyer-intel dashboard` → `dashboard.html`。

**Apollo 金鑰**
- 金鑰已取得並放入 `.env`(曾誤放 `.env.example`,提交前已移出,無外洩)。
- 實測 People Search API 回 403 `API_INACCESSIBLE`:**免費方案不開放此 API**
  (架構報告「免費 tier 驗證流程」的假設僅適用網頁介面)。
- 免費替代路徑:Apollo 網頁搜尋 → 匯出 CSV → `ingest --source manual`。

### 未提交的工作目錄變更(待決定)

- `adapters/apollo.py`:403 改為明確錯誤訊息與替代方案指引
- `README.md`:Apollo 待辦項註記「API 需付費方案」

### 環境備忘

- Python:`uv` 建的 3.12 venv(系統只有 3.9);uv 與 ant、claude symlink
  都在 `~/.local/bin/`
- GitHub:`mike840203/buyer-intelligence`;本地 main 領先 origin 1 個
  commit(`b577fdd`),尚未 push

### 下一步

1. 決定是否提交 apollo.py / README 的未提交變更,並 push 同步 GitHub
2. `buyer-intel review` 覆核 Prima Coffee 草稿
3. `buyer-intel pipeline` 跑剩餘 3 筆種子名單(PNW 兩家預期 A 級,驗證評分模型)
4. Apollo 走網頁匯出 CSV 建立第一批德州+西岸名單(或升級付費方案開 API)
5. 其餘見 README「待辦清單」
