"""L6 送後引擎:核准之後的自動化(排程 → 三輪跟進 → 寄送 → 回覆煞車)。

- schedule.py  排程(美國工作日/假日/寄信時段,純函式)
- footer.py    CAN-SPAM 合規 footer + 簽名(純函式)
- sequence.py  核准後生成三輪信、算排程、入佇列
- dispatcher.py warmup 限流 + 一次寄 1 封 + 可插拔寄送後端 + bounce/回覆煞車
- gmail.py     Gmail API 寄送 + 回覆偵測(選用後端,有憑證才啟用)
"""
