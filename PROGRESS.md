# 專案日誌 — Buyer Intelligence System

> 格式:新條目加在最上面。記錄「做了什麼、決策與理由、卡在哪、下一步」。

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
