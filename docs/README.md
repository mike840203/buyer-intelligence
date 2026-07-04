# 外部工具說明文件

本資料夾詳細介紹系統使用的三個外部資料服務:申請步驟、金鑰設定、
在系統中的角色與用法、方案額度、疑難排解。

| 文件 | 服務 | 在系統中的角色 | 必要性 |
|---|---|---|---|
| [apollo.md](apollo.md) | Apollo.io | **L1 主力名單來源**:用職稱×產業×地區找買家決策人與 email | 名單量產必備 |
| [hunter.md](hunter.md) | Hunter.io | **L2 email 驗證**:寄信前過濾無效信箱,保護網域信譽 | 強烈建議 |
| [google-maps.md](google-maps.md) | Google Maps Places API | **L1 店家掃描**:按城市找獨立咖啡店/廚具零售商 | 建議 |

三者都是可選的:缺金鑰時對應功能會明確報錯,其他功能照常運作。
金鑰一律填在 **`.env`**(不進版控),不要填 `.env.example`。

LLM(Claude)的設定不在此資料夾——見主 [README](../README.md) 的「LLM 後端」一節。
