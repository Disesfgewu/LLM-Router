import os
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


class ModelRouter:
    """
    多模型智能路由器，自動在多個 LLM API 提供者之間做 failover 和配額管理。
    
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
        
        # 模型配置：model_id → RPD 配額 (-1 表示無限制)
        # 順序決定優先級（同 provider 內先列的先試）
        self._config_limits: Dict[str, Any] = {
            "TextOnlyHigh": {
                "GitHub": {
                    "openai/gpt-4o": 50, "openai/gpt-5-mini": 12, "openai/gpt-5": 8,
                    "xai/grok-3": 15, "openai/o1-preview": 8, "xai/grok-3-mini": 30,
                    "deepseek/DeepSeek-R1": 8
                },
                "Google": {
                    "gemini-2.5-flash": 20, "gemini-2.5-flash-lite": 20
                }
            },
            "TextOnlyLow": {
                "GitHub": {"openai/gpt-4o-mini": 150},
                "Google": {"gemini-3.1-flash-lite": 500, "gemma-3-27b": 14400},
                "Ollama": {"qwen3:4b-instruct": -1, "deepseek-r1:1.5b": -1}
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
                    key = f"{cat}|{model_id}"
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
                        self._local_remaining_rpd[f"{cat}|{model_id}"] = rpd_value
            
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

    def _call_with_retry(
        self,
        client: OpenAI,
        model_id: str,
        messages: List[Dict[str, str]],
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
        
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = client.chat.completions.create(
                    model=model_id,
                    messages=messages,  # type: ignore[arg-type]
                    **kwargs
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
        messages: List[Dict[str, str]],
        **kwargs
    ) -> Optional[Any]:
        """
        在指定類別內執行 chat，自動 failover 到下一個可用模型。
        
        Returns:
            OpenAI ChatCompletion response，或 None 如果全部失敗
        """
        model_list = self.priority_map.get(category, [])
        if not model_list:
            logger.warning(f"類別 {category} 沒有配置任何模型")
            return None
        
        start_idx = self.priority_flags.get(category, 0)
        
        # 防止越界：如果 start_idx 超過列表長度，從頭開始
        if start_idx >= len(model_list):
            start_idx = 0
            self._update_priority_flag(category, 0)
        
        for i in range(start_idx, len(model_list)):
            m = model_list[i]
            model_id = m["model_id"]
            provider = m["provider"]
            usage_key = f"{category}|{model_id}"
            
            # 檢查配額
            remaining = self._get_remaining_quota(usage_key)
            if remaining == 0:
                continue
            
            try:
                client = getattr(self, provider.lower())
                
                # Log: 嘗試路由
                user_query = messages[-1].get('content', '') if messages else ""
                query_preview = user_query[:50] + "..." if len(user_query) > 50 else user_query
                logger.info(f"[Route Try] Cat: {category} | Model: {model_id} | Query: {query_preview}")
                
                # 執行呼叫（帶重試）
                response = self._call_with_retry(client, model_id, messages, **kwargs)
                
                # 取得回答
                answer = ""
                if response.choices and response.choices[0].message:
                    answer = response.choices[0].message.content or ""
                
                answer_preview = answer[:100] + "..." if len(answer) > 100 else answer
                logger.info(f"[Success] Provider: {provider} | Model: {model_id} | Answer: {answer_preview}")
                
                # 扣減配額
                self._decrement_quota(usage_key)
                
                # 更新優先順序（下次從這個成功的模型開始）
                self._update_priority_flag(category, i)
                
                return response
                
            except RateLimitError as e:
                logger.error(f"🚫 {model_id} 額度已爆 (RateLimitError)，標記為 0")
                self._mark_quota_exhausted(usage_key)
                self._update_priority_flag(category, i + 1)
                continue
                
            except APIStatusError as e:
                logger.warning(f"[APIError] Model {model_id}: {e.status_code} - {e.message}")
                self._update_priority_flag(category, i + 1)
                continue
                
            except Exception as e:
                logger.warning(f"[Error] Model {model_id} Failed: {type(e).__name__}: {e}")
                self._update_priority_flag(category, i + 1)
                continue
        
        return None

    # ─────────────────────────────────────────────────────────
    # 公開 API
    # ─────────────────────────────────────────────────────────
    def chat(
        self,
        messages: List[Dict[str, str]],
        mode: str = "auto",
        target_category: Optional[str] = None,
        **kwargs
    ) -> Any:
        """
        發送 chat 請求，自動路由到可用的模型。
        
        Args:
            messages: OpenAI 格式的訊息列表
            mode: 路由模式（目前僅支援 "auto"）
            target_category: 指定類別，如 "TextOnlyHigh"、"TextOnlyLow"
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
            
            # 自動模式：先試 High，再試 Low
            res = self._execute_chat("TextOnlyHigh", messages, **kwargs)
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