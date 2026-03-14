import os
import re
import time
import json
import logging
import threading
from typing import Dict, List, Optional, Any

from openai import OpenAI, RateLimitError, APITimeoutError, APIConnectionError, APIStatusError

# --- 設定 Logging ---
os.makedirs("app", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("app/app.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ModelRouter")


# --- 常數配置 ---
DEFAULT_TIMEOUT = 60          # API 請求 timeout (秒)
MAX_RETRIES = 2               # 網路錯誤最大重試次數
RETRY_DELAY = 1.0             # 重試間隔 (秒)
PROVIDER_ORDER = ["GitHub", "Google", "Ollama"]  # 優先順序
MULTIMODAL_INTENT_RE = re.compile(
    r"(ocr|圖片|图像|影像|照片|截圖|截图|掃描|扫描|看圖|看图|image|vision|visual|pdf|csv|xlsx|excel|文件|檔案|document)",
    re.IGNORECASE,
)
IMAGE_GENERATION_INTENT_RE = re.compile(
    r"(生成圖片|生成图像|生圖|生图|畫圖|画图|image\s*generation|generate\s+image|draw\s+an\s+image|imagen)",
    re.IGNORECASE,
)


class ModelRouter:
    """
    多模型智慧路由器，自動在多個 LLM API 提供者之間做 failover 和配額管理。
    
    Features:
        - 多提供者路由 (GitHub Models, Google Gemini, Ollama)
        - 自動 Failover：一個模型失敗或額度滿，自動切換下一個
        - 配額追蹤 (RPD)：本地追蹤每個模型的每日請求數
        - Thread-safe：支援多執行緒同時呼叫
        - Retry with backoff：網路瞬斷自動重試
    """
    
    def __init__(self, usage_db_path: str = "usage_tracker.json"):
        self.usage_db_path = usage_db_path
        self._lock = threading.Lock()
        
        # Lazy-loaded clients
        self._google_client: Optional[OpenAI] = None
        self._github_client: Optional[OpenAI] = None
        self._ollama_client: Optional[OpenAI] = None

        self._model_capabilities: Dict[str, Dict[str, Any]] = {
            "gemini-2.5-flash": {
                "chat_capable": True,
                "image_input": True,
                "document_input": True,
                "preferred_tasks": ["ocr", "vision", "multimodal_analysis"],
            },
            "gemini-2.5-flash-lite": {
                "chat_capable": True,
                "image_input": True,
                "document_input": False,
                "preferred_tasks": ["vision"],
            },
            "gemma-3-27b-it": {
                "chat_capable": True,
                "image_input": True,
                "document_input": False,
                "preferred_tasks": ["ocr", "vision", "multimodal_analysis"],
            },
            "gemini-2.5-flash-tts": {
                "chat_capable": False,
                "task": "tts",
            },
            "imagen-4-generate": {
                "chat_capable": False,
                "task": "image_generation",
            },
            "imagen-4-ultra-generate": {
                "chat_capable": False,
                "task": "image_generation",
            },
            "imagen-4-fast-generate": {
                "chat_capable": False,
                "task": "image_generation",
            },
        }
        
        # 會話歷史記憶（保留最近的對話）
        self.conversation_history: List[Dict[str, str]] = []
        self.max_history_size: int = 10  # 最多保留10輪對話
        
        # 模型配置：model_id → RPD 配額 (-1 表示無限制)
        # 順序決定優先級（同 provider 內先列的先試）
        self._config_limits: Dict[str, Any] = {
            "TextOnlyHigh": {
                "GitHub": {
                    "openai/gpt-4o": 50,
                    "xai/grok-3": 15,
                    "deepseek/DeepSeek-R1": 8,
                },
                "Google": {
                    "gemini-2.5-flash": 20, "gemini-2.5-flash-lite": 20
                }
            },
            "ChatOnly": {
                # 推理型模型：適合簡單對話，但複雜任務可能因 token 限制導致空答案
                "GitHub": {
                    "openai/gpt-5-mini": 12,
                    "xai/grok-3-mini": 30,
                    "openai/gpt-5": 8,  # token 限制太嚴格，經常返回空答案
                    # "openai/o1-preview": 8,  # API 版本錯誤：需要 2024-12-01-preview
                }
            },
            "TextOnlyLow": {
                "GitHub": {"openai/gpt-4o-mini": 150},
                "Google": {"gemini-3.1-flash-lite-preview": 500, "gemma-3-27b-it": 14400},
                "Ollama": {"qwen3:4b-instruct": -1, "deepseek-r1:1.5b": -1}
            },
            "MultiModal": {
                "Google": {
                    "gemini-2.5-flash": 20,
                    "gemma-3-27b-it": 14400,
                }
            }
        }
        
        # 動態建立所有類別的 priority_flags
        self.priority_flags: Dict[str, int] = {
            cat: 0 for cat in self._config_limits.keys()
        }
        self.priority_map: Dict[str, List[Dict[str, str]]] = {}
        self._local_remaining_rpd: Dict[str, int] = {}

        self._load_usage_db()
        self._build_priority_map()
        logger.info("🚀 ModelRouter 初始化完成。")

    def get_usage_key(self, provider: str, model_id: str) -> str:
        return f"{provider}|{model_id}"

    # ─────────────────────────────────────────────────────────
    # 配額管理
    # ─────────────────────────────────────────────────────────
    def _load_usage_db(self) -> None:
        """從 JSON 檔案載入配額狀態。"""
        if os.path.exists(self.usage_db_path):
            try:
                with open(self.usage_db_path, 'r', encoding='utf-8') as f:
                    self._local_remaining_rpd = json.load(f)
                # 同步：補上 config 有但 DB 沒有的新模型
                self._sync_new_models()
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"載入配額檔案失敗，重置: {e}")
                self.reset_all_quotas()
        else:
            self.reset_all_quotas()

    def _sync_new_models(self) -> None:
        """將 config 中新增的模型同步到配額 DB（不影響已有的）。"""
        updated = False
        for cat, providers in self._config_limits.items():
            for provider, models_dict in providers.items():
                for model_id, rpd_value in models_dict.items():
                    key = self.get_usage_key(provider, model_id)
                    if key not in self._local_remaining_rpd:
                        self._local_remaining_rpd[key] = rpd_value
                        updated = True
                        logger.info(f"新增模型配額: {key} = {rpd_value}")
        if updated:
            self._save_usage_db()

    def _save_usage_db(self) -> None:
        """將配額狀態存入 JSON 檔案（需在 lock 內呼叫）。"""
        try:
            with open(self.usage_db_path, 'w', encoding='utf-8') as f:
                json.dump(self._local_remaining_rpd, f, indent=2)
        except IOError as e:
            logger.error(f"儲存配額檔案失敗: {e}")

    def _build_priority_map(self) -> None:
        """建立每個類別的模型優先順序列表。"""
        for cat in self._config_limits.keys():
            ordered_list: List[Dict[str, str]] = []
            cat_data = self._config_limits.get(cat, {})
            
            for provider in PROVIDER_ORDER:
                if provider in cat_data:
                    for model_id in cat_data[provider].keys():
                        ordered_list.append({
                            "provider": provider,
                            "model_id": model_id
                        })
            
            self.priority_map[cat] = ordered_list

    def reset_all_quotas(self) -> None:
        """每日大重置：將所有模型的 RPD 配額重置為最大值。"""
        logger.info("📅 執行 RPD 每日大重置...")
        
        with self._lock:
            for cat, providers in self._config_limits.items():
                for provider, models_dict in providers.items():
                    for model_id, rpd_value in models_dict.items():
                        # rpd_value 直接是數字 (-1 表示無限制)
                        self._local_remaining_rpd[self.get_usage_key(provider, model_id)] = rpd_value
            
            # 重置所有類別的優先順序指標
            self.priority_flags = {cat: 0 for cat in self._config_limits.keys()}
            self._save_usage_db()

    def refresh_rpm_limit(self) -> None:
        """半小時重置：重置優先順序指標，讓之前跳過的模型可以再被嘗試。"""
        logger.info("🕒 執行 RPM 半小時重置指標...")
        
        with self._lock:
            self.priority_flags = {cat: 0 for cat in self._config_limits.keys()}
            self._save_usage_db()

    # ─────────────────────────────────────────────────────────
    # Client Properties (Lazy Loading)
    # ─────────────────────────────────────────────────────────
    @property
    def google(self) -> OpenAI:
        if self._google_client is None:
            api_key = os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                logger.warning("GOOGLE_API_KEY 未設定，Google 後端將無法使用")
            self._google_client = OpenAI(
                api_key=api_key or "dummy",
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                timeout=DEFAULT_TIMEOUT
            )
        return self._google_client

    @property
    def github(self) -> OpenAI:
        if self._github_client is None:
            api_key = os.environ.get("GITHUB_MODELS_API_KEY")
            if not api_key:
                logger.warning("GITHUB_MODELS_API_KEY 未設定，GitHub 後端將無法使用")
            self._github_client = OpenAI(
                api_key=api_key or "dummy",
                base_url="https://models.github.ai/inference",
                timeout=DEFAULT_TIMEOUT
            )
        return self._github_client

    @property
    def ollama(self) -> OpenAI:
        if self._ollama_client is None:
            self._ollama_client = OpenAI(
                api_key="ollama",
                base_url="http://localhost:11434/v1",
                timeout=DEFAULT_TIMEOUT
            )
        return self._ollama_client

    # ─────────────────────────────────────────────────────────
    # 核心路由邏輯
    # ─────────────────────────────────────────────────────────
    def _get_remaining_quota(self, usage_key: str) -> int:
        """Thread-safe 取得剩餘配額。"""
        with self._lock:
            return self._local_remaining_rpd.get(usage_key, 0)

    def _decrement_quota(self, usage_key: str) -> None:
        """Thread-safe 扣減配額。"""
        with self._lock:
            current = self._local_remaining_rpd.get(usage_key, 0)
            if current != -1:  # -1 表示無限制
                self._local_remaining_rpd[usage_key] = current - 1
                self._save_usage_db()

    def _mark_quota_exhausted(self, usage_key: str) -> None:
        """Thread-safe 標記配額用盡。"""
        with self._lock:
            self._local_remaining_rpd[usage_key] = 0
            self._save_usage_db()

    def _update_priority_flag(self, category: str, index: int) -> None:
        """Thread-safe 更新優先順序指標。"""
        with self._lock:
            self.priority_flags[category] = index

    def _flatten_content_for_log(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "image_url":
                        parts.append("[image]")
                        continue
                    text = part.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                        continue
                elif isinstance(part, str):
                    parts.append(part)
            return "\n".join(parts)
        return str(content)

    def _extract_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        stripped = (text or "").strip()
        if not stripped:
            return None
        candidates = [stripped]
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start:end + 1])
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def decide_multimodal_category(
        self,
        raw_messages: List[Dict[str, Any]],
        request_profile: Dict[str, Any],
    ) -> Optional[str]:
        """Use a cheap model once to decide whether expensive multimodal routing is needed."""
        latest_user_text = str(request_profile.get("latest_user_text", ""))
        has_image_input = bool(request_profile.get("has_image_input"))
        has_file_input = bool(request_profile.get("has_file_input"))
        file_kinds = request_profile.get("file_kinds", [])

        if IMAGE_GENERATION_INTENT_RE.search(latest_user_text):
            logger.info("[MultimodalDecision] image generation intent detected, but chat endpoint stays text-oriented; skip MultiModal chat routing")
            return None

        if not has_image_input and not has_file_input and not MULTIMODAL_INTENT_RE.search(latest_user_text):
            return None

        heuristic_default = has_image_input
        decision_messages = [
            {
                "role": "system",
                "content": (
                    "你是多模態路由分類器。"
                    "請判斷當前請求是否必須使用昂貴的多模態聊天模型。"
                    "只有在真的需要理解圖片、OCR、截圖內容、掃描檔內容時才 use_multimodal=true。"
                    "如果只是 txt/csv/xlsx/pdf 被系統預先轉成文字摘要，通常 use_multimodal=false。"
                    "如果是 image generation 或 TTS 類需求，目前 chat endpoint 不直接走該類模型，請 use_multimodal=false。"
                    "只能輸出單一 JSON 物件。"
                    '{"use_multimodal": true|false, "task": "text_only|ocr|vision|document_analysis|image_generation|tts", "reason": "..."}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"latest_user_text: {latest_user_text or '(empty)'}\n"
                    f"has_image_input: {has_image_input}\n"
                    f"has_file_input: {has_file_input}\n"
                    f"file_kinds: {file_kinds}\n"
                ),
            },
        ]

        try:
            response = self._execute_chat(
                "TextOnlyLow",
                decision_messages,
                temperature=0.0,
                max_tokens=160,
            )
            if response and response.choices and response.choices[0].message:
                content = response.choices[0].message.content or ""
                parsed = self._extract_json_object(content)
                if parsed and parsed.get("use_multimodal") is True:
                    task = str(parsed.get("task", ""))
                    if task == "image_generation":
                        logger.info("[MultimodalDecision] classifier detected image generation, skip MultiModal chat routing")
                        return None
                    logger.info("[MultimodalDecision] use_multimodal=True task=%s", task)
                    return "MultiModal"
                logger.info("[MultimodalDecision] classifier returned text-only")
        except Exception as exc:
            logger.warning("[MultimodalDecision] classifier failed, fallback to heuristics: %s", exc)

        return "MultiModal" if heuristic_default else None

    def _prepare_kwargs(self, model_id: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """
        根據模型調整參數。某些模型有特殊的參數限制。
        
        gpt-5 和 o1 系列是推理模型，只支持少數參數。
        """
        # 需要使用 max_completion_tokens 的模型前綴
        models_need_completion_tokens = [
            "openai/gpt-5", 
            "openai/o1"
        ]
        
        # 推理模型：只支持特定參數（只能用默認值）
        reasoning_models = [
            "openai/gpt-5",
            "openai/o1"
        ]
        
        prepared_kwargs = kwargs.copy()
        
        # 檢查是否為推理模型
        is_reasoning_model = any(model_id.startswith(prefix) for prefix in reasoning_models)
        needs_completion_tokens = any(model_id.startswith(prefix) for prefix in models_need_completion_tokens)
        
        if is_reasoning_model:
            # 推理模型只支持以下參數：
            # - max_completion_tokens (建議設置，否則可能返回空答案)
            # - messages (必需)
            # 移除所有其他參數
            
            allowed_params = set()
            removed_params = []
            
            # 保留允許的參數
            if "max_tokens" in prepared_kwargs or "max_completion_tokens" in prepared_kwargs:
                # 轉換 max_tokens 為 max_completion_tokens
                if "max_tokens" in prepared_kwargs:
                    token_value = prepared_kwargs.pop("max_tokens")
                    prepared_kwargs["max_completion_tokens"] = token_value
                    logger.debug(f"[參數轉換] {model_id} max_tokens={token_value} → max_completion_tokens={token_value}")
                allowed_params.add("max_completion_tokens")
            else:
                # 如果沒有設置，使用更大的默認值（推理模型需要大量 token 用於思考）
                # 推理模型會先用一部分 token 進行推理，再用剩餘的生成實際輸出
                prepared_kwargs["max_completion_tokens"] = 32000
                logger.info(f"[參數設置] {model_id} 未指定 max_completion_tokens，使用默認值 32000（推理+輸出）")
            
            # 移除所有不支援的參數
            unsupported_params = ["temperature", "top_p", "frequency_penalty", "presence_penalty", 
                                 "n", "stop", "logprobs", "top_logprobs"]
            
            for param in unsupported_params:
                if param in prepared_kwargs:
                    removed_value = prepared_kwargs.pop(param)
                    removed_params.append(f"{param}={removed_value}")
            
            if removed_params:
                logger.info(f"[參數清理] {model_id} 是推理模型，移除不支援的參數: {', '.join(removed_params)}")
        
        elif needs_completion_tokens and "max_tokens" in prepared_kwargs:
            # 非推理模型但需要轉換參數
            prepared_kwargs["max_completion_tokens"] = prepared_kwargs.pop("max_tokens")
            logger.debug(f"[參數轉換] {model_id} 使用 max_completion_tokens 而非 max_tokens")
        
        return prepared_kwargs
    
    def _call_with_retry(
        self,
        client: OpenAI,
        model_id: str,
        messages: List[Dict[str, Any]],
        **kwargs
    ) -> Any:
        """
        執行 API 呼叫，帶有重試機制。
        
        Raises:
            RateLimitError: 配額用盡
            APIStatusError: 其他 API 錯誤
            Exception: 重試後仍失敗
        """
        last_error: Optional[Exception] = None
        
        # 根據模型調整參數
        prepared_kwargs = self._prepare_kwargs(model_id, kwargs)
        
        # 調試：記錄實際發送的參數
        logger.info(f"[API調用] {model_id} 最終參數: {prepared_kwargs}")
        
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = client.chat.completions.create(
                    model=model_id,
                    messages=messages,  # type: ignore[arg-type]
                    **prepared_kwargs
                )
                return response
                
            except (APITimeoutError, APIConnectionError) as e:
                # 網路/超時錯誤：可重試
                last_error = e
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAY * (attempt + 1)
                    logger.warning(
                        f"[Retry {attempt + 1}/{MAX_RETRIES}] "
                        f"Model {model_id}: {type(e).__name__}, 等待 {delay}s..."
                    )
                    time.sleep(delay)
                continue
                
            except RateLimitError:
                # Rate limit：不重試，直接往上拋
                raise
                
            except APIStatusError:
                # 其他 API 錯誤：不重試
                raise
        
        # 重試次數用盡
        raise last_error or RuntimeError(f"Model {model_id} 呼叫失敗")

    def _execute_chat(
        self,
        category: str,
        messages: List[Dict[str, Any]],
        **kwargs
    ) -> Optional[Any]:
        """
        在指定類別內執行 chat，自動 failover 到下一個可用模型。
        
        Ollama 作為最後備選：先嘗試所有非 Ollama 模型，都用完後才用 Ollama。
        
        Returns:
            OpenAI ChatCompletion response，或 None 如果全部失敗
        """
        model_list = self.priority_map.get(category, [])
        if not model_list:
            logger.warning(f"類別 {category} 沒有配置任何模型")
            return None
        
        # 分離 Ollama 和非 Ollama 模型
        non_ollama_models = [m for m in model_list if m["provider"] != "Ollama"]
        ollama_models = [m for m in model_list if m["provider"] == "Ollama"]
        
        start_idx = self.priority_flags.get(category, 0)
        
        # 防止越界：如果 start_idx 超過非 Ollama 列表長度，從頭開始
        if non_ollama_models and start_idx >= len(non_ollama_models):
            start_idx = 0
            self._update_priority_flag(category, 0)
        
        # 第一輪：輪詢所有非 Ollama 模型（GitHub、Google）
        if non_ollama_models:
            logger.info(f"[輪詢階段1] 嘗試非 Ollama 模型（共 {len(non_ollama_models)} 個）")
            result = self._try_models(
                non_ollama_models, 
                category, 
                start_idx, 
                messages, 
                is_ollama_phase=False,
                **kwargs
            )
            if result:
                return result
        
        # 第二輪：只有當非 Ollama 模型都不可用時，才使用 Ollama
        if ollama_models:
            logger.info(f"[輪詢階段2] 非 Ollama 模型已用完，嘗試 Ollama 模型（共 {len(ollama_models)} 個）")
            result = self._try_models(
                ollama_models,
                category,
                0,  # Ollama 總是從頭開始
                messages,
                is_ollama_phase=True,
                **kwargs
            )
            if result:
                return result
        
        # 所有模型都試過了
        logger.warning(f"[輪詢結束] 類別 {category} 的所有模型都不可用或配額已滿")
        return None

    def _try_models(
        self,
        model_list: List[Dict[str, str]],
        category: str,
        start_idx: int,
        messages: List[Dict[str, Any]],
        is_ollama_phase: bool = False,
        **kwargs
    ) -> Optional[Any]:
        """
        輪詢嘗試一組模型。
        
        Args:
            model_list: 要嘗試的模型列表
            category: 類別名稱
            start_idx: 起始索引
            messages: 訊息列表
            is_ollama_phase: 是否為 Ollama 階段
            **kwargs: 其他參數
            
        Returns:
            成功的 response 或 None
        """
        attempted_count = 0
        for offset in range(len(model_list)):
            i = (start_idx + offset) % len(model_list)
            m = model_list[i]
            model_id = m["model_id"]
            provider = m["provider"]
            usage_key = self.get_usage_key(provider, model_id)
            attempted_count += 1
            
            # 檢查配額
            remaining = self._get_remaining_quota(usage_key)
            if remaining == 0:
                continue
            
            try:
                client = getattr(self, provider.lower())
                
                # Log: 嘗試路由
                user_query = messages[-1].get('content', '') if messages else ""
                flattened_query = self._flatten_content_for_log(user_query)
                query_preview = flattened_query[:50] + "..." if len(flattened_query) > 50 else flattened_query
                logger.info(f"[Route Try] Cat: {category} | Model: {model_id} | Query: {query_preview}")
                
                # 執行呼叫（帶重試）
                response = self._call_with_retry(client, model_id, messages, **kwargs)
                
                # 取得回答（詳細調試）
                answer = ""
                if response.choices:
                    if len(response.choices) > 0:
                        choice = response.choices[0]
                        if hasattr(choice, 'message') and choice.message:
                            answer = choice.message.content or ""
                            if not answer:
                                logger.warning(f"[調試] {model_id} message.content 為空")
                        else:
                            logger.warning(f"[調試] {model_id} choice 沒有 message 屬性")
                else:
                    logger.warning(f"[調試] {model_id} response 沒有 choices")
                
                # 如果答案為空，記錄完整的 response 結構（用於調試）
                if not answer:
                    logger.warning(f"[調試] {model_id} 完整 response: {response}")
                    
                    # 檢查是否因為 token 限制導致空答案
                    if hasattr(response, 'usage') and response.usage:
                        usage = response.usage
                        if hasattr(usage, 'completion_tokens_details'):
                            details = usage.completion_tokens_details
                            if hasattr(details, 'reasoning_tokens') and details.reasoning_tokens > 0:
                                logger.error(
                                    f"[Token限制] {model_id} 使用了 {details.reasoning_tokens} reasoning tokens，"
                                    f"但只分配了 {usage.completion_tokens} completion tokens，"
                                    f"導致沒有剩餘 token 生成輸出。GitHub Models 可能對此模型有硬性限制。"
                                )
                    
                    logger.warning(f"[空回答] {model_id} 返回空答案，跳過並嘗試下一個模型")
                    # 仍然扣減配額，因為 API 調用成功了
                    self._decrement_quota(usage_key)
                    # 更新索引到下一個模型
                    if not is_ollama_phase:
                        next_idx = (i + 1) % len(model_list)
                        self._update_priority_flag(category, next_idx)
                    continue
                
                answer_preview = answer[:100] + "..." if len(answer) > 100 else answer
                logger.info(f"[Success] Provider: {provider} | Model: {model_id} | Answer: {answer_preview}")
                
                # 扣減配額
                self._decrement_quota(usage_key)
                
                # 更新優先順序為下一個模型（只在非 Ollama 階段更新）
                if not is_ollama_phase:
                    # 在非 Ollama 模型列表中計算下一個索引
                    next_idx = (i + 1) % len(model_list)
                    self._update_priority_flag(category, next_idx)
                    logger.info(f"[輪詢] 下次將從索引 {next_idx} 開始（非 Ollama 模型，共 {len(model_list)} 個）")
                else:
                    logger.info(f"[Ollama] 使用 Ollama 模型，不更新優先級")
                
                return response
                
            except RateLimitError as e:
                logger.error(f"🚫 {model_id} 額度已爆 (RateLimitError)，標記為 0")
                self._mark_quota_exhausted(usage_key)
                if not is_ollama_phase:
                    next_idx = (i + 1) % len(model_list)
                    self._update_priority_flag(category, next_idx)
                continue
                
            except APIStatusError as e:
                logger.warning(f"[APIError] Model {model_id}: {e.status_code} - {e.message}")
                if not is_ollama_phase:
                    next_idx = (i + 1) % len(model_list)
                    self._update_priority_flag(category, next_idx)
                continue
                
            except Exception as e:
                logger.warning(f"[Error] Model {model_id} Failed: {type(e).__name__}: {e}")
                if not is_ollama_phase:
                    next_idx = (i + 1) % len(model_list)
                    self._update_priority_flag(category, next_idx)
                continue
        
        logger.info(f"[輪詢] 嘗試了 {attempted_count} 個模型，都不可用")
        return None

    # ─────────────────────────────────────────────────────────
    # 公開 API
    # ─────────────────────────────────────────────────────────
    def chat(
        self,
        messages: List[Dict[str, Any]],
        mode: str = "auto",
        target_category: Optional[str] = None,
        include_chat_only: bool = False,
        reverse_order: bool = False,
        **kwargs
    ) -> Any:
        """
        發送 chat 請求，自動路由到可用的模型。
        
        Args:
            messages: OpenAI 格式的訊息列表
            mode: 路由模式（目前僅支援 "auto"）
            target_category: 指定類別，如 "TextOnlyHigh"、"TextOnlyLow"、"ChatOnly"、"MultiModal"
            include_chat_only: auto 模式是否包含 ChatOnly 推理模型（僅對 chat completions）
            reverse_order: auto 模式是否反轉順序（Low→High，用於 legacy completions）
            **kwargs: 傳遞給 OpenAI API 的其他參數
            
        Returns:
            OpenAI ChatCompletion response
            
        Raises:
            RuntimeError: 所有模型皆不可用
        """
        try:
            if target_category:
                # 指定類別
                res = self._execute_chat(target_category, messages, **kwargs)
                if res:
                    return res
                raise RuntimeError(f"類別 {target_category} 內所有模型皆不可用或額度已滿")
            
            # 自動模式
            if reverse_order:
                # Legacy completions: Low → High（不包含 ChatOnly）
                res = self._execute_chat("TextOnlyLow", messages, **kwargs)
                if res:
                    return res
                
                res = self._execute_chat("TextOnlyHigh", messages, **kwargs)
                if res:
                    return res
            else:
                # Chat completions: High → ChatOnly（可選）→ Low
                res = self._execute_chat("TextOnlyHigh", messages, **kwargs)
                if res:
                    return res
                
                # 如果啟用 ChatOnly，在 High 和 Low 之間嘗試
                if include_chat_only:
                    res = self._execute_chat("ChatOnly", messages, **kwargs)
                    if res:
                        return res

                res = self._execute_chat("TextOnlyLow", messages, **kwargs)
                if res:
                    return res
            
            raise RuntimeError("💀 所有模型皆不可用！")
            
        except RuntimeError:
            raise
        except Exception as e:
            logger.critical(f"[Critical] Chat Error: {type(e).__name__}: {e}")
            raise

    # ─────────────────────────────────────────────────────────
    # 記憶功能
    # ─────────────────────────────────────────────────────────
    def add_to_history(self, user_message: str, assistant_response: str) -> None:
        """將對話添加到歷史記錄中。"""
        with self._lock:
            self.conversation_history.append({
                "role": "user",
                "content": user_message
            })
            self.conversation_history.append({
                "role": "assistant", 
                "content": assistant_response
            })
            
            # 保持歷史記錄在限定大小內（每輪對話2條消息）
            if len(self.conversation_history) > self.max_history_size * 2:
                self.conversation_history = self.conversation_history[-(self.max_history_size * 2):]

    def get_last_exchange(self) -> tuple[Optional[str], Optional[str]]:
        """獲取最後一輪對話（問題和回答）。"""
        with self._lock:
            if len(self.conversation_history) >= 2:
                last_user_msg = None
                last_assistant_msg = None
                
                # 從後往前找
                for msg in reversed(self.conversation_history):
                    if msg["role"] == "assistant" and last_assistant_msg is None:
                        last_assistant_msg = msg["content"]
                    elif msg["role"] == "user" and last_user_msg is None:
                        last_user_msg = msg["content"]
                    
                    if last_user_msg and last_assistant_msg:
                        break
                
                return last_user_msg, last_assistant_msg
            return None, None

    def check_need_log_rag(self, user_message: str) -> bool:
        """
        Pre-chat: 使用 gemma-3-27b-it 判斷是否需要查詢 log 資訊。
        
        Args:
            user_message: 用戶輸入的消息
            
        Returns:
            True 表示需要查 log，False 表示不需要
        """
        # 關鍵字列表
        memory_keywords = ["記憶", "memory", "查看過去", "剛剛", "之前", "先前", "上次", "log", "日誌", "歷史", "記錄"]
        
        # 簡單關鍵字匹配（大小寫不敏感）
        user_message_lower = user_message.lower()
        has_keyword = any(keyword.lower() in user_message_lower for keyword in memory_keywords)
        
        if not has_keyword:
            logger.info(f"[Pre-chat] 未檢測到記憶相關關鍵字，跳過 RAG")
            return False
        
        # 準備 prompt
        prompt = f"""你是一個分類器。請判斷以下用戶問題是否需要查詢過去的系統日誌（app.log）來回答。

用戶問題：{user_message}

判斷標準：
- 如果問題涉及查看過去的記錄、日誌、歷史、記憶、之前的對話等，回答 true
- 如果問題是普通的對話或問答，不需要查詢歷史，回答 false

請只回答 true 或 false，不要有其他內容。"""

        # 使用 gemma-3-27b-it
        model_id = "gemma-3-27b-it"
        usage_key = self.get_usage_key("Google", model_id)
        
        try:
            # 檢查配額
            remaining = self._get_remaining_quota(usage_key)
            if remaining == 0:
                logger.warning(f"[Pre-chat] {model_id} 配額已用完，使用關鍵字匹配")
                return has_keyword
            
            # 調用模型
            response = self.google.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=10
            )
            
            # 成功後扣減配額
            self._decrement_quota(usage_key)
            logger.info(f"[Pre-chat] {model_id} 調用成功，配額剩餘: {self._get_remaining_quota(usage_key)}")
            
            result = (response.choices[0].message.content or "").strip().lower()
            logger.info(f"[Pre-chat] {model_id} 判斷結果: {result}")
            
            return "true" in result
            
        except RateLimitError as e:
            logger.error(f"[Pre-chat] {model_id} 配額用完: {e}")
            self._mark_quota_exhausted(usage_key)
            return has_keyword
                
        except Exception as e:
            logger.error(f"[Pre-chat] {model_id} 調用失敗: {e}，使用關鍵字匹配")
            return has_keyword

    def read_app_log(self, max_lines: int = 100) -> str:
        """
        讀取 app.log 的最後 N 行。
        
        Args:
            max_lines: 最多讀取的行數
            
        Returns:
            log 內容字符串
        """
        log_path = "app/app.log"
        try:
            if not os.path.exists(log_path):
                return "[log 文件不存在]"
            
            with open(log_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                # 取最後 max_lines 行
                recent_lines = lines[-max_lines:] if len(lines) > max_lines else lines
                return "".join(recent_lines)
        except Exception as e:
            logger.error(f"讀取 log 文件失敗: {e}")
            return f"[讀取 log 失敗: {e}]"

            raise