# 專案日誌 — Buyer Intelligence System

> 格式:新條目加在最上面。記錄「做了什麼、決策與理由、卡在哪、下一步」。

---

## 2026-07-10(晚)Hunter 網域反查接進 pipeline + 版控整理提交

1. **Hunter Domain Search 自動補聯絡人**(解「Google 地圖掃來的店缺 email」):
   `enrich.find_contacts_by_domain()` 網域反查決策人,依部門/資深度/可信度
   排序挑「最該寄的人」;`backfill_contacts()` 設主收件人+驗證、其餘存
   alt_contacts;只找到通用信箱(hello@/info@)則作退路。
   接在 graph node_score **之後**:僅對「A/B 級 + 缺 email + 有網站」反查,
   C 級/T3 不花額度。實測 Dark Matter 反查到 VP Brian(可信度 97)+9 備選;
   Gaslight 取 hello@ 退路。Places adapter 同步修:加 nextPageToken 翻頁
   (單查詢 20→~60 家)。
2. **Google Places 翻頁修復**:原只取第一頁 20 筆,漏做翻頁;現靠
   nextPageToken 湊到 ~60 家(Google 單查詢上限)。
3. **版控整理**:修 `.gitignore`——`imports/` 行內註解導致規則失效(個資
   CSV 差點入庫)、`data/` 收斂為整個資料夾忽略(擋 .dispatcher_paused 等
   本機狀態)。確認 .env / 名單 CSV / leads.db 全擋、.env.example 無真金鑰。
   本次提交涵蓋本 session 與平行 session 的全部成果(Gmail 寄送、company
   profile、排程、Hunter 反查),測試 20→41 全通過。

---

## 2026-07-10(下)Gmail 實測 + 測試信進 UI + 三份文件全面改版

1. **Gmail OAuth 全程接通**:Cloud Console 憑證 → 本機一次性授權接收器
   (localhost:8765 換 refresh token,免 OAuth Playground)→ .env 寫入 →
   `SENDING_BACKEND=gmail`。API 實測寄件身分 = mike.chen.test01@gmail.com。
   ⚠️ 待辦:OAuth app 仍為「測試」狀態,**refresh token 7 天過期,本週內要按
   「發布應用程式」**。
2. **端對端寄信驗證成功——但測試信進垃圾郵件**:成因=全新帳號零信譽 +
   免費信箱無法自訂 SPF/DKIM/DMARC + 主旨帶 TEST 且與先前手寄測試信相似
   (Gmail 相似度連坐)。定調:非系統缺陷,是寄達率基建課題;
   對策成文於《上線作戰手冊》。
3. **🧪 寄測試信進 Web UI**(/outbox):走完整鏈路但 `test=True`——不佔
   warmup 額度、不啟動暖機時鐘、豁免 interval 護欄、不動 lead 階段;
   pipeline 亦跳過 🧪 測試 lead(不浪費研究額度)。QueuedEmail 加 test 欄位。
4. **公司 profile 補完**:sender.email=mike.chen.test01(測試期)、
   地址=因益達科技(ankomn.tw 官網,林口)轉英文格式、電話補上。
   footer 合規要件齊備,/outbox 地址警告消除。
5. **三份文件全面改版**:
   - `docs/README.md` → 《上線作戰手冊》:垃圾桶四道判定解剖、三階段寄達率
     作戰計畫(Phase 0 測試帳號 / Phase 1 專用網域+Workspace+DNS+暖機 4-6 週 /
     Phase 2 監控)、上線前總 checklist(🔴⬜🔹)、技術債 roadmap(1a 心跳/
     1b launchd/2 重試/3 API 後端/4-7)、日常 SOP
   - `plan/ankomn_strategy_report.html` → Final v3:執行摘要更新(L6/Gmail/
     通用化)、新增「柒之二 寄達率戰略」、時間軸與風險表更新(寄達率風險
     升級為已驗證+新增排程器單點風險)、KPI 加退信率/spam 率、驗算加第 7 條;
     商業數字(名單數學/預算/KPI)維持 v2 簽核口徑不動
   - `plan/buyer_intelligence_architecture.html` → v3.0 as-built:新增 Profile 層
     與 L6 送後引擎完整章(狀態機/排程演算/dispatcher 規則表/雙後端/回覆偵測/
     測試信)、設計原則第 7 條「確定性歸程式,判斷歸 LLM」、資料模型/UI/CLI/
     測試(38 項)/成本/技術債全面更新
   - 交叉檢查:HTML 結構合法、關鍵數字(38 測試/600 A級/90 筆週/預算口徑/
     暖機時程)三文件一致;根 README 補《上線作戰手冊》入口
6. **退訂機制升級(使用者質疑「該有按鈕吧?」觸發)**:範例 repo 同樣無按鈕
   (reply UNSUBSCRIBE,它明文解釋無法部署 web endpoint;我方處境相同且合規)。
   但補上更好的解:(a)每封信帶 `List-Unsubscribe: <mailto:...?subject=UNSUBSCRIBE>`
   標頭 → Gmail 在信件頂部顯示**原生取消訂閱按鈕**(免 web endpoint,寄達率
   正面訊號);(b)補 exportlab 有而我方漏的 reply_keyword 機制:回信含
   UNSUBSCRIBE/退訂等關鍵字 → 自動加退訂名單 + 歸檔 + 取消剩餘跟進。
7. **🧪 測試升級為完整三輪序列,且走人工覆核閘門**(使用者兩次修正需求:
   「複合信件要變成測試的一環」+「就算是測試也要人工覆核」):按鈕 → 三輪
   測試「草稿」進覆核佇列(與真名單同畫面、可改稿)→ 人按「核准整串」→
   enqueue_for_lead 偵測 🧪 lead → 壓縮時程 0/+2/+4 分鐘(真實 +4/+6 工作日)
   + test=True 入佇列,seq2/3 接同一 thread(Re: + threadId)。配套:
   (a)測試 seq2/3 豁免「seq1 尚未寄出」暫緩;(b)_thread_ref_of_seq1 取最新;
   (c)重測時舊排程自動作廢;(d)退訂名單直接拒絕;(e)覆核頁對測試 lead
   顯示「+2/+4 分鐘(測試壓縮)」標籤。驗證清單五項寫進 UI 卡片。
8. **UI 修正**:名單頁篩選下拉改「即選即生效」(原需再按篩選鈕,體感像壞掉)
   + 篩選狀態列;/outbox 全頁動態區每 10 秒自動刷新(輸入中暫停)、已寄出列
   顯示 📩 已回信 / 🚫 已退訂 標籤;操作手冊(docs/操作手冊.md,非技術人員版)
   新增並掛上索引。
9. 測試 38 → 41 綠燈(List-Unsubscribe 標頭、三輪測試序列、限流豁免斷言);
   回覆偵測閉環實測通過(測試 lead 回信 → 自動 followed_up + 取消跟進)。

---

## 2026-07-10 L6 送後引擎 + 全系統通用化(參考 exportlab-outreach 的自動化設計)

背景:研讀 exportlab-tw/exportlab-outreach(Accio skill 版 cold email 全自動工具),
結論是「找名單/寫信品質我方較強,寄送後的自動化它較強」→ 移植其後段設計,
守住我方 human-in-the-loop 閘門。

1. **鐵律改寫**:「絕不直接寄信」→「人工核准後進自動寄送」。閘門不動
   (人仍逐封審核),自動化的只有「送出的手」。
2. **通用化(company profile 層)**:公司身分全部抽到 `company/ankomn.toml`
   (寄件人/價值主張/campaign/競品),`company.py` 載入、`COMPANY_PROFILE`
   env 可切換 —— outreach/enrich/scoring/brief 原本寫死 Ankomn 的地方全改
   profile 驅動。今天 Ankomn、明天塑膠代工,換 toml 即可。
3. **三輪序列(L4 擴充)**:pipeline 出稿時同時預生成 seq2(價值信,+4 工作日)
   /seq3(graceful 收尾,+6 工作日),覆核頁三封一起看、一次核准
   (使用者選的「核准一次涵蓋整串」模式)。反幻覺規則比 exportlab 嚴:
   禁止編造統計/案例/客戶名,只准用 L2 查到的事實。
4. **L6 送後引擎(新 sending/ 套件)**:
   - `schedule.py`:美國聯邦假日(2026-27)+ 州別→時區 + buyer 當地
     09:30–16:30 時段 + 工作日 offset 演算(純函式)
   - `sequence.py`:核准 → 三輪信附 CAN-SPAM footer 入 email_queue,
     同日排程自動錯開 60–90 分鐘
   - `dispatcher.py`:每輪只寄最早 1 封;warmup 每日上限(2/天→5/天);
     全域節奏護欄(距上封 <60 分不寄);寄前重查退訂/lead 狀態;
     同網域 3 次失敗自動整域退訂;拿到 message id 才標 sent
   - `gmail.py`:Gmail API 後端(REST+refresh token,零新依賴)+
     thread 回覆偵測 → 自動觸發煞車。**預設 SENDING_BACKEND=eml 乾跑**
     (排程/限流全真實運作,「送出」輸出 .eml 由人寄),網域暖機好再切 gmail
   - 回覆煞車:apply_track 任何推進事件(replied/meeting/…)自動取消
     佇列中未寄出的跟進信
5. **Web UI 全監控**:新增 /outbox 寄送佇列頁(今日用量/warmup 階段、
   佇列明細+單封取消、排程器即時日誌、暫停/恢復、手動跑一輪、退訂管理);
   覆核頁三輪化;lead 詳情顯示排程;儀表板加寄送佇列卡;serve 啟動背景
   排程器(60 秒輪詢)。CLI 加 `buyer-intel dispatch`。
6. **資料層**:email_queue + unsubscribed 兩張新表;「清空全部」會清佇列
   但**保留退訂名單**(合規承諾)。
7. 測試 20 → 38(排程數學/footer 要件/入佇列錯開/一次一封/warmup 限流/
   回覆煞車/寄前守門/seq1 失敗時跟進暫緩全覆蓋),全過;Web UI 14 條路由
   冒煙全過。docs/gmail.md 新增 OAuth 申請指南。
8. **全面檢視修正**(換模型接手後 code review):(a) 多 lead 跟進信同時刻
   排程會被 60 秒輪詢連發 → 加全域節奏護欄(距上封 <interval_min 不寄);
   (b) seq1 失敗時 seq2/3 原會被永久取消 → 改「暫緩」保留 ready;
   (c) /outbox 時間顯示改 buyer 當地(原誤轉美東);(d) 閒置輪詢日誌去重;
   (e) 假日表缺年份時 /outbox 顯示警告;(f) 失敗信可一鍵重排;
   (g) 通用化殘留清理:split_subject 寫死展會主旨、enrich 品類關鍵字、
   dashboard/UI logo/CLI 描述全改 profile 驅動。

⚠️ 上線前必辦:(1) `company/ankomn.toml` 的 sender.address 填真實英文地址
(CAN-SPAM 必填,gmail 模式會擋);(2) 專用寄信網域 + SPF/DKIM/DMARC +
暖機(README M2 待辦);(3) mail-tester 測分 ≥7 再切 gmail 後端。

---

## 2026-07-07~08 州名化 + 來源歸因 + 資料治理 + 工具鏈全通

1. **地區概念退場,UI 全面州名化**:50 州全名自動正規化為縮寫
   (修掉 Apollo「Illinois」被歸 OTHER 的 bug);「地區」僅存於評分權重內部。
2. **來源標籤系統**:CSV 匯入時自選來源(Apollo/LinkedIn/Stockists/IHA/手動),
   名單列表顯示州+來源欄;pipeline 可依**州 × 來源**篩選(下拉附筆數)。
   教訓:掃描城市由使用者當下指定,不依戰略文件自行展開(西雅圖事件)。
3. **Google 地圖掃描進 Web UI**:匯入頁自訂搜尋句(「業態 in 城市, 州」);
   GOOGLE_MAPS_API_KEY 實測可用(新金鑰有數分鐘生效延遲屬正常)。
4. **Hunter 接通 + 驗證時機修正**:Free 100 次/月;驗證移至 pipeline L2 背景
   (曾在匯入時同步驗證導致 UI 卡住逾一分鐘);手動改信箱/換人即時驗單筆。
5. **資料品質防線(回應 Tim 對 Apollo 資料的疑慮)**:Hunter 攔無效信箱
   (離職者信箱多半停用)+ 覆核頁/詳情頁「LinkedIn 在職查核」連結
   (Google 搜尋合規查核,不爬蟲)+ 備援聯絡人一鍵切換。
6. **資料治理**:主頁危險區「清空全部」(輸入 DELETE 雙重確認、任務中禁用、
   保留 imports/)+ 單筆刪除(確認框)。
7. **併發修復**:平行 pipeline 撞出 database is locked——根因是多 worker
   同時初始化全新 checkpoints.db;修:建圖全程持鎖 + checkpoint 連線
   timeout=30 + leads.db 開 WAL。壓測 6 執行緒同時初始化 0 錯誤。
8. **claude CLI 斷鏈永久修復**:改裝獨立版(官方 install.sh 自我更新)+
   config 解析加存在性驗證與 VSCode 擴充路徑自癒後備。
9. `plan/` 併入 repo;四份文件(README/兩份 plan/本檔)同步至本節所有變更。
   測試 18/18。

---

## 2026-07-05(二)claude CLI 斷鏈修復(永久解法)

- 症狀:pipeline 全批秒失敗「找不到 claude CLI」。
- 根因:昨日的 `~/.local/bin/claude` 是指向 VSCode 擴充 2.1.199 內建執行檔
  的手工符號連結;擴充自動更新到 2.1.201 時舊資料夾被刪,連結懸空。
- 永久解法(兩層):(1)改裝**獨立版 Claude Code**(官方 install.sh,
  自我更新機制管理版本,不依賴 VSCode);(2)`config.py` 解析邏輯加
  存在性驗證 + VSCode 擴充路徑自動搜尋(取最新版)作為自癒後備。
- 失敗的 17 筆零額度損失(秒失敗未呼叫 LLM),重跑 pipeline 即可。

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
