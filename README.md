# CHRIS 投資早報完整版本

保留既有 CHRIS 白底、藍色品牌與漲綠跌紅設定，加入市場板塊、走勢圖、MOVE、Reuters 美股新聞與獨立波動率頁。

## 上傳 GitHub

將壓縮檔解壓縮，把以下檔案放到 max19890121/chris-investment-brief 相同路徑，覆蓋原檔：

- index.html
- volatility.html
- assets/volatility.css
- assets/volatility.js
- scripts/update_brief.py
- scripts/update_volatility.py
- scripts/validate_data.py
- .github/workflows/chris-auto-update.yml（隱藏資料夾內）
- data/brief.json
- data/volatility.json
- data/history/ 下的檔案

不用刪除原本的歷史檔案。Mac Finder 用 Command + Shift + . 顯示 .github 資料夾。GitHub 網頁 Add file → Upload files 可上傳一般資料夾；workflow 可在既有 .github/workflows/chris-auto-update.yml 編輯頁完整覆蓋。

提交後，到 Actions → CHRIS Auto Update → Run workflow。排程為台灣時間週一至週五 07:05。程式需要既有 Actions 的 Contents 寫入設定才能提交資料。

這份交付為本機程式碼；尚未寫入線上 GitHub。

## 美股新聞來源備援

依 Reuters → Bloomberg → Yahoo Finance 順序，透過 Google News RSS 取得美股新聞；來源錯誤、沒有最近七天的有效消息或來源驗證不符時，改用下一個。驗證 publisher 名稱及來源網域，保留原始英文標題、台灣發布時間、新聞入口。只顯示最近七天、最多十則，不保留固定新聞作為備援。

畫面顯示實際採用的媒體來源；三個來源都失敗時顯示 SOURCE ERROR，不保留舊新聞偽裝新消息。這是 Google News RSS 索引，不是媒體官方授權 API。連結先經 Google News 再進入報導；可能受到 Reuters 的訂閱或存取政策限制。空清單顯示 NO FEED，抓取失敗顯示 SOURCE ERROR；不冒充已取得 Reuters 正文，也不自動生成新聞摘要或翻譯。

## 已實作板塊

歷史報告、核心指數、BLUF、VIX/MOVE/CNN 情緒、五區全球焦點、Reuters 消息、美股／亞洲／歐洲、歐洲債券利差、十一產業、美債、外匯、能源、貴金屬、觀察區間、BLS 實際數據、經濟日程、FOMC、Fed 目標、FedWatch、CHRIS 規則觀點、五個資產歷史圖、核心觀察與來源狀態。

獨立波動率頁有 20/30/60/90 日 HV、90 日報酬／最大回撤、20/90 比率、平均／中位／最高／最低、Top40/Bottom20、十一產業統計、分布、公司名稱搜尋、產業複選、排序與個股歷史圖。

## 資料限制

- 名單來自 SPY 與 QQQ 官方 ETF 持股，並非官方指數成分股名單。產業來自 SPDR 產業 ETF 持股分類代理，未分類者不推估。
- 波動率以 Yahoo 調整後日收盤價計算；缺失不插值、不跨越缺口計算。價格與資料日期均可檢查。
- MOVE 來自 Investing.com 歷史表，驗證 ICE BofAML MOVE 名稱、日期、價格及發布漲跌；不是自行估計。
- 歐洲殖利率、FedWatch 機率及公司基本資料尚無可靠資料 feed，顯示 NO FEED。
- 本次 CNN、BLS 行事曆、黃金／白銀現貨與 ECB 部分來源無法取得，明示 SOURCE ERROR。期貨與現貨分開，不以期貨取代現貨。
- 非農為 BLS 就業總人數；沒有來源的市場預測不填入。
- 觀察區間是歷史最小／最大值，不是價格目標；透明規則只描述輸入訊號，不宣稱新聞因果。
- 每日第一份已產生報告保存於 history；不補造過去報告。

## 驗證

實際抓取成功：Reuters 十則，MOVE、VIX、Treasury、EFFR、五資產歷史，以及 518 檔持股名單中的 514 檔完整波動率資料。

通過 JSON schema、有限數值、同日利差、唯一股票名單、HV 獨立公式、缺口處理、前端完整渲染、Reuters 來源／安全連結與失敗備援檢查。JavaScript 語法檢查通過。

瀏覽器預覽工具本次逾時，因此尚未完成實際畫面視覺檢查。
