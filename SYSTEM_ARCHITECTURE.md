# 系統架構文件

本文是本專案的正式架構說明，目標是回答三件事：

- 系統目前由哪些模組組成
- 從使用者請求到回傳答案，實際經過哪些步驟
- 設計目標與現況差距在哪裡

## 1. 架構總覽

本系統是多模型 API gateway，對外提供 OpenAI 相容介面，內部整合路由、搜尋、審核與多模態流程。

核心設計原則：

- 單一入口：所有主要能力以同一批 API 端點提供
- 任務導向路由：先判斷任務意圖，再決定模型與流程
- 可補救流程：回答品質不足時允許補查與重寫
- 保守運行：避免無上限迭代造成成本與延遲失控

## 2. 核心模組與責任

### 2.1 API 協調層

檔案：[api.py](api.py)

責任：

- 接收與驗證請求
- 統一處理附件與多模態輸入
- 呼叫意圖分類、搜尋決策、審核回圈
- 組裝與回傳 OpenAI 相容格式

主要端點：

- /v1/chat/completions
- /v1/completions
- /v1/direct_query
- /v1/images/generations
- /v1/file/generate_content
- /v1/models
- /admin/status
- /admin/logs
- /mcp/sse
- /mcp/messages

### 2.2 模型路由層

檔案：[ModelRouter/ModelRouter.py](ModelRouter/ModelRouter.py)

責任：

- 管理 provider/account/model 註冊
- 根據任務類別與配額進行路由
- 失敗時進行有限重試與備援切換
- 記錄配額與內部 helper 用量統計

目前 provider：Google、GitHub、HuggingFace、Ollama。

目前主要任務類別：TextOnlyHigh、ChatOnly、TextOnlyLow、MultiModal、ImageGeneration。

### 2.3 搜尋與工具輔助層

檔案：[app/tools.py](app/tools.py)、[app/search.py](app/search.py)

責任：

- 判斷是否需要外部搜尋
- 產生搜尋規劃任務
- 處理 tool-calling round trip
- 萃取 citations 與清理工具結果
- 提供回答完整性 reviewer

### 2.4 訊息整形層

檔案：[app/messages.py](app/messages.py)、[app/response.py](app/response.py)

責任：

- 訊息正規化與裁切
- 工具訊息清理與安全保底
- 回應格式封裝與輸出整形

### 2.5 多模態前處理

檔案：[app/multimodal.py](app/multimodal.py)

責任：

- 圖片與文件輸入整形
- 附件類型辨識與可處理格式轉換
- 協助決定是否進入多模態路徑

### 2.6 前端操作層

檔案：[frontend/src/App.jsx](frontend/src/App.jsx)、[frontend/src/components/ChatInterface.jsx](frontend/src/components/ChatInterface.jsx)

責任：

- 提供對話、儀錶板、日誌 UI
- 呼叫 API 並顯示文字與圖片結果
- 附件上傳與使用參數操作

## 3. 端到端流程

### 3.1 設計目標流程（閉環）

目標是建立可自我修正的閉環流程，而不是一次生成就直接回傳。

目標步驟：

1. 接收請求
- 入口：/v1/chat/completions
- 輸入：使用者 prompt、messages、附件、可選工具與參數

2. Pre-Chat 分類
- 使用 Gemma 先判斷任務意圖
- 產出 intent：text_chat、multimodal、memory_query、image_generation

3. 路由與執行
- 根據 intent 走對應執行分支
- 若需外部資訊，先走搜尋判斷與搜尋規劃

4. 品質驗證
- reviewer 檢查答案是否完整、正確、可用

5. 失敗回圈
- 若 reviewer 不通過，帶入失敗理由與缺漏資訊
- 重新分類、重路由、重執行，直到通過

6. 輸出封裝
- 清理輸出、附來源與必要欄位
- 回傳 OpenAI 相容格式結果

這個設計才是完整 reasoning 導向的 agentic loop。

### 3.2 目前實作流程（現況）

目前已經是「可多輪補救」，但不是無上限閉環。

### 3.3 現況詳細執行步驟（逐步）

以下描述的是程式目前實際行為。

#### Step A. 入口與請求解析

- 解析 JSON body，讀取 model、messages、stream、tools、tool_choice、max_tokens 等欄位。
- 將 top-level 附件欄位（input_files、input_images）注入到訊息結構。
- 若是多模態輸入，會先做預處理與格式統一。

輸出：

- raw_messages（已注入附件）
- multimodal_profile（是否含圖、檔案類型、最後 user 文字）

#### Step B. 意圖分類（Pre-Chat）

- 呼叫 classify_intent 進行單一入口分類。
- 主要分類模型：gemma-3-27b-it。
- 若模型失敗或不可用，退回關鍵字 fallback。

輸出：

- intent_result.intent：text_chat、multimodal、memory_query、image_generation
- intent_result.multimodal_format：image、document、null

#### Step C. 意圖分支處理

- image_generation：走生圖流程（必要時可加資料型搜尋補強）。
- memory_query：允許啟用記憶路徑，可能注入歷史 log 內容。
- multimodal：走附件分析路徑。
- text_chat：走一般文字回答路徑。

#### Step D. 搜尋與工具流程（條件式）

條件一：請求宣告 tools。

- 先做搜尋決策（_llm_decide_web_search）。
- 若需要，做搜尋規劃（_llm_plan_web_search_tasks）。
- 回傳 tool_calls 或進入 post-tool 合成流程。

條件二：未宣告 tools，但判定問題需要外部資訊。

- 走主動搜尋流程：decide -> plan -> search -> evidence 注入。

使用模型：

- 搜尋決策：gemma-3-27b-it
- 搜尋規劃：gemma-3-27b-it

#### Step E. 訊息整理與路由前保護

- messages 正規化、裁切、估算 token。
- 保證至少保留一則有效 user 訊息（避免下游 API 無內容錯誤）。
- 清理工具雜訊與不安全轉錄內容。

#### Step F. 模型路由與主回答生成

- 由 ModelRouter 根據 target_category、配額、可用 provider/account/model 進行選擇。
- 執行回答生成。

輸出：

- draft answer（第一版答案）
- model_used（實際使用模型）

#### Step G. reviewer 迭代補救（有限多輪）

- 觸發條件：通常在 text_chat 且有可用 evidence 時。
- reviewer 檢查答案完整性（_llm_review_answer_completeness）。
- 若不完整：
	- 依 next_queries 做補查
	- 將「審核不通過原因 + 缺漏 + 補充證據」加入下一輪
	- 重新做 intent 分類與重寫
- 迭代停止條件：
	- reviewer 通過
	- 無可用 next_queries
	- 補查無新 evidence
	- 達到 max_review_iterations 上限

目前參數：

- max_review_iterations 預設 3
- max_review_iterations 上限 6

#### Step H. 輸出後處理與回傳

- 進行語言/格式清理。
- 組裝 OpenAI 相容 response。
- 若有來源，附 citations；若為生圖，附 images。
- 支援一般回應與串流回應。

### 3.4 現況與目標的差距

已做到：

- 分類、搜尋、規劃、審核、補查、重寫都已串起來
- 已支援有限多輪 reviewer 回圈

尚未做到：

- 無上限直到通過的完整閉環
- 依不同 intent 套用不同 reviewer 規範與停止策略

## 4. 分類模型與關鍵輔助模型

目前主流程使用的分類與審核模型如下：

- 意圖分類模型：gemma-3-27b-it
- 搜尋決策模型：gemma-3-27b-it
- 搜尋規劃模型：gemma-3-27b-it
- 回答 reviewer 模型：gemma-3-27b-it

補充：

- 若 Gemma 路徑失敗，部分步驟會退回其他路徑或關鍵字 fallback。

## 5. 資料與狀態管理

目前採單機檔案與記憶體狀態：

- 使用量檔案：[usage_tracker.json](usage_tracker.json)
- 系統日誌：app/app.log
- Router 記憶體狀態：priority、client、helper usage 計數

優點：部署簡單、觀測直接。  
限制：不適合多實例共享與分散式一致性需求。

## 6. 可靠性與失敗處理

目前保護策略：

- 類別與模型備援切換
- 有限次 API 重試
- 訊息裁切與安全保底（避免無效請求）
- reviewer 有上限迭代，避免成本失控

注意：

- 並非所有錯誤都可自動恢復
- 若請求依賴特定能力且候選不足，仍可能失敗

## 7. 已知邊界

以下是目前應被視為邊界的事項：

- 搜尋與研究品質仍在調校，不是穩定 SLA
- 多輪補救是有上限的，不是無上限自我修正
- 單機 usage tracking 不等於集中式配額系統
- 前端屬操作與監控介面，不是完整工作流平台

## 8. 後續建議

若要更接近完整 reasoning agent，建議優先做：

- 把 reviewer 失敗後的下一輪策略由固定模板升級為策略化規劃
- 讓不同 intent 使用不同 reviewer 標準
- 加入回圈中止條件的品質分數與成本分數
- 將多輪歷史決策結構化，避免資訊漂移

## 9. 建議閱讀順序

1. [README.md](README.md)
2. [api.py](api.py)
3. [ModelRouter/ModelRouter.py](ModelRouter/ModelRouter.py)
4. [app/tools.py](app/tools.py)
5. [API_USAGE_GUIDE.md](API_USAGE_GUIDE.md)

前端除錯再補看：

- [frontend/src/App.jsx](frontend/src/App.jsx)
- [frontend/src/components/ChatInterface.jsx](frontend/src/components/ChatInterface.jsx)