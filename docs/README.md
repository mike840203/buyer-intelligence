# 上線作戰手冊 — 寄達率、養帳號與上線前全部待辦

> **本文件的定位**:系統怎麼「跑起來」看根目錄 [README](../README.md);
> **日常怎麼操作(給非技術人員)看 [操作手冊](操作手冊.md)**;
> 本文件回答的是「怎麼讓寄出去的信**進收件匣而不是垃圾桶**、正式上線前還缺什麼」。
> 文末附各外部工具(Apollo / Hunter / Google Maps / ImportYeti / Gmail)的申請指南索引。

**現況快照(2026-07-10)**

| 項目 | 狀態 |
|---|---|
| 系統本體(L1–L6 + Web UI + 排程器) | ✅ 完成,38 項測試綠燈 |
| Gmail 自動寄送(OAuth) | ✅ 已接通(`mike.chen.test01@gmail.com`) |
| 第一封端對端測試信 | ✅ 已自動寄達 —— **但進了垃圾郵件** |

最後一格就是本手冊存在的原因。**測試信進垃圾桶不是故障,是教材**:它把「寄達率」
這個全戰略最大的實務風險,從文件上的一行字變成你親眼看到的事實。
下面第一章解剖它為什麼進垃圾桶,第二章給出完整的作戰計畫。

---

## 一、為什麼信會進垃圾桶:Gmail 的四道判定

Gmail(以及所有主流信箱)決定一封信去收件匣還是垃圾桶,看四件事:

| 判定支柱 | 看什麼 | 我們的測試信在這項的得分 |
|---|---|---|
| **1. 寄件者身分認證** | SPF / DKIM / DMARC 三個 DNS 紀錄——證明「你真的是你」 | 免費 `@gmail.com` 有 Google 代簽的基本認證,但**無法自訂**;正式做 cold email 必須自有網域才能完整設定 |
| **2. 寄件者信譽** | 這個帳號/網域/IP 的歷史:寄過多少、多少被開、多少被檢舉 | ❌ **全新帳號 = 零信譽**。零信譽 + 突然對外寄信 = 最典型的 spam 模式 |
| **3. 內容訊號** | 主旨與內文像不像垃圾信:全大寫、spam 字眼、與已知垃圾信相似 | ❌ 主旨帶 `TEST`、內文與**先前被歸類為垃圾的信相似**(Gmail 明說了——就是你更早手動寄的那封 .eml 測試信) |
| **4. 收件人互動** | 收件人開信?回信?標非垃圾?還是刪除/檢舉? | ❌ 還沒有任何正向互動歷史 |

**解剖這次的判定**:四道裡我們輸了三道(2、3、4),而且第 3 道還踩了「與先前
垃圾信相似」的連坐——第一封手動測試被標進垃圾桶後,第二封同內容的信
直接繼承了判決。這在正式營運是致命循環:**一旦開始進垃圾桶,後面的信會越來越難出來。**

> 推論一條鐵則:**寄達率是先建後寄,不是寄了再修。** 這就是戰略時間軸把
> 「寄信基建」列為 8 月關鍵路徑、標注「不可壓縮」的原因。

---

## 二、寄達率作戰計畫(三個階段)

### Phase 0 — 測試帳號,現在就做(成本 0,今天 10 分鐘)

目的:讓「跑通流程」的測試不再進垃圾桶,同時理解互動訊號怎麼運作。

1. **把那封測試信標「回報為非垃圾郵件」**——這直接訓練收件帳號的過濾器,
   下一封同寄件人的信大機率進收件匣
2. **建立雙向互動**:從 `mike410123024` **回信**給 `mike.chen.test01`,再互寄一兩封
   normal 內容的信。Gmail 對「有來有往的通訊對象」幾乎不擋
3. **測試主旨別再用 TEST/測試字眼**——系統的 🧪 測試信主旨已含 `TEST`,
   拿來驗證鏈路 OK,但驗證「會不會進垃圾桶」時,先把寄件人加入收件帳號的通訊錄
4. **跑一次 [mail-tester.com](https://www.mail-tester.com)**:用 /outbox 的測試信
   功能,把收件人改成 mail-tester 給的一次性地址,寄出後看評分。
   **記下這個基準分**,Phase 1 完成後要求 ≥ 9

> Phase 0 的極限:再怎麼做,免費 `@gmail.com` + 零歷史帳號寄 cold email
> 給陌生人,進垃圾桶機率仍然偏高。**這個帳號的定位是流程驗證與練習**,
> 不是正式火力。

### Phase 1 — 正式寄信基建(上線前必做;對應戰略時間軸 2026-08)

這是「進收件匣」的真正解法,五步有嚴格順序:

**Step 1:買一個專用寄信網域(~US$12/年)**

- **不要用主網域**(ankomn.com):cold email 一旦傷信譽,會連累公司正常郵件
- 買一個相近網域,例:`ankomn-intl.com`、`ankomnusa.com`、`tryankomn.com`
- 業界慣例:寄信網域被燒掉就換一個,主網域永遠乾淨

**Step 2:Google Workspace 綁定該網域(~US$7/月/使用者)**

- 建立正式寄件信箱,例:`mike@ankomn-intl.com`
- 比免費 Gmail 多拿到:自訂 DNS 認證的能力、更高的寄送上限(2000/天)、
  以及 **Google Postmaster Tools**(免費監控你網域在 Gmail 眼中的信譽分)

**Step 3:設定三個 DNS 紀錄(30 分鐘,一次性)**

在網域 DNS 加三筆(Workspace 後台會給精確值,以下是形狀):

```
SPF   —— TXT @                  "v=spf1 include:_spf.google.com ~all"
DKIM  —— TXT google._domainkey  (Workspace 後台產生的一長串公鑰)
DMARC —— TXT _dmarc             "v=DMARC1; p=quarantine; rua=mailto:dmarc@你的網域"
```

設完到 [mail-tester.com](https://www.mail-tester.com) 驗證,**目標 ≥ 9/10**。
低於 9 不要開始暖機——先修到位。

**Step 4:網域暖機 4–6 週(不可壓縮的等待期)**

Gmail 沒有官方「暖機」機制——暖機的本質是**用漸進的量 + 正向互動,
把零信譽養成好信譽**。計畫表:

| 週次 | 每天寄 | 寄給誰 | 動作要求 |
|---|---|---|---|
| 第 1–2 週 | 2–5 封 | **會回你的人**:同事、朋友、自己的其他信箱 | 對方要開信、回信、標星——製造正向互動 |
| 第 3–4 週 | 5–10 封 | 混入少量真實但低風險對象(認識的業界朋友) | 維持回覆率,監控 Postmaster |
| 第 5–6 週 | 10–15 封 | 開始小量真 cold(系統 warmup 模式 2 封/天起) | 退信率 < 2%、無 spam 檢舉 |
| 之後 | 交給系統 | 系統 warmup 節奏(2/天 → 5/天)接手 | 系統每日上限 + 錯開 + 時段防線全程生效 |

> 也可以用暖機服務(Mailwarm、Warmup Inbox 等,~$15–30/月)自動化前四週
> 的互寄互回;量小的話手動就夠。

**Step 5:換 OAuth 到新帳號(10 分鐘)**

新 Workspace 信箱啟用後,照 [gmail.md](gmail.md) 重跑一次 OAuth,更新 `.env`
三個值 + `company/*.toml` 的 `sender.email`,重啟 serve 即切換完成。

### Phase 2 — 持續衛生(開始正式寄信後,每週 10 分鐘)

**內容規則(系統已把關大半,人工覆核時再確認):**
- 個人化理由真實(只用 L2 查到的事實——Opus 審稿會擋,人是最後防線)
- 純文字、無多連結、無附件、無 hype 詞(FREE!、100% guaranteed…)
- 合規 footer(實體地址 + UNSUBSCRIBE 指示)——系統自動附
- **List-Unsubscribe 標頭**——每封信自帶,Gmail 會在信件頂部顯示原生
  「取消訂閱」按鈕(對寄達率是正面訊號);對方按了或回信含 UNSUBSCRIBE,
  系統自動加入退訂名單並歸檔
- 同一人最多 3 封、回覆即停——系統強制

**名單衛生(退信率是信譽殺手):**
- 只寄 Hunter 驗證通過或 accept_all 的信箱(系統 L2 自動驗)
- B2B 名單年腐化 25–30%,超過 3 個月的名單重驗再寄
- 系統 bounce 防線:同網域 3 次失敗自動整域退訂

**監控指標(每週看一次):**

| 指標 | 健康值 | 看哪裡 | 超標動作 |
|---|---|---|---|
| 退信率 | < 2% | /outbox failed 計數 | 立即暫停,檢查名單來源 |
| 回覆率 | 3–10%(cold 正常區間) | 儀表板漏斗 | 低於 2% 檢討信件內容與名單契合度 |
| Spam 檢舉率 | < 0.1% | Google Postmaster Tools | 任何檢舉都要回頭看是哪批名單 |
| 網域信譽 | High / Medium | Postmaster Tools | 掉到 Low 立即停寄 1–2 週 |

---

## 三、上線前總 Checklist

依急迫程度排序。🔴 = 有時限,⬜ = 上線前必做,🔹 = 建議做。

### 🔴 有時限(本週內)

- [ ] **發布 OAuth 應用程式**:Google Cloud Console → OAuth 同意畫面 →
  「發布應用程式(PUBLISH APP)」。**測試狀態的 refresh token 7 天自動過期**,
  不發布的話 7 天後所有自動寄送靜默失效。發布不需通過 Google 審查(30 秒)
- [ ] Phase 0 全部四項(標非垃圾、互寄互回、通訊錄、mail-tester 基準分)

### ⬜ 正式 outreach 前必做(對應戰略 8 月關鍵路徑)

- [ ] 專用寄信網域購買(Phase 1 Step 1)
- [ ] Google Workspace 開通 + 正式寄件信箱(Step 2)
- [ ] SPF / DKIM / DMARC 設定 + mail-tester ≥ 9(Step 3)
- [ ] 暖機 4–6 週(Step 4;**8 月啟動才趕得上 9 月名單期**)
- [ ] OAuth 換綁正式帳號 + `company/*.toml` 更新 sender(Step 5)
- [ ] Google Postmaster Tools 接上網域監控
- [ ] `company/ankomn.toml` 最終確認:寄件人署名(現為 The Ankomn Team)、
  地址(現為林口公司登記地址)、`booking_url` 填 Calendly 連結
- [ ] **技術債 #1:排程器獨立化**(見第四章)——「核准後自動寄」的可靠性支柱
- [ ] **技術債 #2:寄送重試 + 退避**——Gmail 暫時性錯誤不該斷三輪節奏

### 🔹 建議(提升產出品質)

- [ ] 技術債 #1a 心跳警示(30 分鐘,先於 #1 完整版)
- [ ] Calendly 建「TIHS 攤位會議 30 分鐘」活動 → 填 `company/*.toml` 的 `booking_url`
- [ ] 競品 Stockists 名單整理(Fellow / Planetary Design「Where to Buy」→ CSV 匯入;
  最高含金量免費來源)
- [ ] ImportYeti 掃 T0 經銷商(見 [importyeti.md](importyeti.md))
- [ ] IHA 會員註冊 + gia 獎項截止日確認(戰略決策請求第 4 項)
- [ ] LLM 後端評估切 `api`(技術債 #3;量產期觸發)

---

## 四、技術債 Roadmap(工程項,按還債順序)

| # | 債 | 問題 | 修法 | 工程量 | 觸發時機 |
|---|---|---|---|---|---|
| 1a | **排程器心跳警示** | 排程器 thread 悄悄死掉時無人知曉(silent failure) | 每輪寫心跳進 DB;UI 偵測「距上次心跳 > 5 分」顯示紅色橫幅 | 30 分鐘 | **現在** |
| 1b | **排程器獨立化** | 寄送依賴 `serve` 終端機視窗開著;闔筆電 = 停擺 | macOS launchd 跑獨立 dispatcher(開機自動、掛掉自動重拉);UI 退為純監控 | 半天 | 正式 outreach 前 |
| 2 | **寄送重試 + 退避** | Gmail 暫時性 429/5xx 直接標 failed,三輪節奏斷裂;且會誤觸 bounce 退訂 | 區分暫時/永久錯誤;暫時性 → `+15min × 2^attempts` 重排(≤3 次);僅永久失敗計入 bounce | 2–3 小時 | 正式 outreach 前 |
| 3 | **LLM 切 API 後端** | `claude -p` subprocess 冷啟動慢、錯誤靠 parse stdout、CLI 曾兩次斷鏈 | `.env` 改 `LLM_BACKEND=api` + 儲值(雙後端已實作,一行切換) | 5 分鐘 + $30–80/月 | 一天 10+ 筆或 CLI 再斷鏈 |
| 4 | LLM 層無迴歸測試 | prompt 改壞只能人工發現 | 錄放式 fixture 測試 | 半天 | 頻繁調 prompt 時 |
| 5 | SQLite 併發 | workers > 5 可能再撞鎖(已有 WAL + timeout 緩解) | 撞到再處理 | — | 撞到時 |
| 6 | Profile lru_cache | 改 toml 要重啟 serve 才生效 | UI 加「重載 profile」按鈕 | 15 分鐘 | 順手時 |
| 7 | 無 schema migration | 未來破壞性改欄位會痛 | 改欄位前先寫 migration 腳本 | — | 改 schema 時 |

---

## 五、日常操作 SOP(系統就緒後的一天)

```
早上(10 分鐘):
  1. 開 http://localhost:8000 —— 看儀表板:逾期警示、覆核佇列數
  2. /review 覆核新草稿:讀 seq1/2/3、點 LinkedIn 查核、按「核准整串」
  3. /outbox 掃一眼:今日用量、排程中、有無 failed(有就看原因/重排)

系統自動(全天):
  排程器 60 秒輪詢 → 到期寄 1 封(warmup 限流 + 錯開)→ 偵測回信自動煞車

每週(10 分鐘):
  1. Phase 2 監控指標四項
  2. 匯入新名單(Apollo 匯出 / 地圖掃描 / Stockists)→ 跑 pipeline
  3. 對照戰略「每週 90 筆」軌道
```

---

## 附:外部工具申請指南索引

| 文件 | 服務 | 在系統中的角色 | 必要性 |
|---|---|---|---|
| [gmail.md](gmail.md) | Gmail API(OAuth) | **L6 自動寄送後端** + thread 回覆偵測 | 自動寄送必備(乾跑模式不用) |
| [apollo.md](apollo.md) | Apollo.io | **L1 主力名單來源**:職稱×產業×地區找決策人與 email | 名單量產必備 |
| [hunter.md](hunter.md) | Hunter.io | **L2 email 驗證** + 網域反查決策人,保護寄件信譽 | 強烈建議 |
| [google-maps.md](google-maps.md) | Google Maps Places API | **L1 店家掃描**:按城市找獨立咖啡店/廚具零售商 | 建議 |
| [importyeti.md](importyeti.md) | ImportYeti(海關提單) | **L1 T0 經銷商來源**:找「會進口」的公司 | 建議(免費) |

金鑰一律填 **`.env`**(不進版控)。缺金鑰時對應功能明確報錯,其餘照常運作。
LLM(Claude)設定見主 [README](../README.md)「LLM 後端」一節。
