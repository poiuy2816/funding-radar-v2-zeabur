# funding-radar-v2-zeabur
# Funding Radar V2 Zeabur

Binance U 本位永續合約資金費率雷達。

## 功能

- Binance U 本位永續合約資金費率掃描
- Binance Spot 可交易性檢查
- 24h 成交額過濾
- OI 過濾
- Order Book 滑點估算
- Basis 現貨 / 合約價差檢查
- 7 天歷史資金費率穩定度
- SQLite 紀錄
- Telegram 推播與指令
- Streamlit UI
- 半自動下單意圖
- Zeabur Docker 部署

## Zeabur 部署

1. 將本 repo 上傳到 GitHub
2. Zeabur 新增 Project
3. 選 GitHub repo 部署
4. 使用 Dockerfile
5. 設定 Volume 到：

```text
/app/data
```

6. 設定環境變數：

```env
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx
DB_PATH=/app/data/radar.db
UI_PASSWORD=your_password
ENABLE_TRADING=false
DRY_RUN=true
```

## Telegram 指令

```text
/help
/status
/top
/pause
/resume
/blacklist_add SYMBOL reason
/blacklist_remove SYMBOL
/order SYMBOL amount
/confirm CODE
/cancel CODE
```

## 安全提醒

預設：

```env
ENABLE_TRADING=false
DRY_RUN=true
```

不會實盤下單。

本版本半自動下單為安全架構版，實盤下單區塊預設保護，不直接發送真實下單。
如要實盤，請先加入完整狀態機、精度處理、部分成交處理、緊急平倉與小額測試。
