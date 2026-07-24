"""
Ollama API client for LLM agent communication
"""
import requests
import json
import hashlib
import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Constants
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 200
DEFAULT_REPEAT_PENALTY = 1.1
DEFAULT_REPEAT_LAST_N = 128
DEFAULT_MIN_P = 0.05
API_TIMEOUT = 300
CONNECTION_CHECK_TIMEOUT = 5
# P1-B: ネットワーク失敗のリトライ（サイレント劣化の是正）。同一 per-call シードで再試行するため
# 成功時の再現性は保たれる（リトライは決定性に影響しない）。
DEFAULT_MAX_RETRIES = 3          # 追加試行回数（総試行 = 1 + max_retries）
DEFAULT_RETRY_BACKOFF = 1.5      # 指数バックオフの基準秒（delay = base * 2**attempt）


class OllamaClient:
    """Client for interacting with Ollama API"""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        repeat_penalty: float = DEFAULT_REPEAT_PENALTY,
        repeat_last_n: int = DEFAULT_REPEAT_LAST_N,
        min_p: float = DEFAULT_MIN_P,
        seed: Optional[int] = None,
        num_ctx: Optional[int] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF,
    ):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.repeat_penalty = repeat_penalty
        self.repeat_last_n = repeat_last_n
        self.min_p = min_p
        # Phase 0: 再現性のための基準シード。None なら従来通り非再現（Ollama 既定）。
        # 呼び出しごとのシードは prompt（seed_key 指定時は seed_key）から決定的に導出する
        # （並列実行順に依存しない）。
        self.seed = seed
        self.num_ctx = num_ctx
        self.api_url = f"{self.base_url}/api/generate"
        # P1-B: リトライ設定と失敗計測（サイレント劣化の可視化）。
        self.max_retries = int(max_retries)
        self.retry_backoff = float(retry_backoff)
        self.call_count = 0            # generate() 呼び出し総数
        self.failure_count = 0         # リトライを使い切って空を返した回数
        self.empty_response_count = 0  # 接続は成功したが空応答だった回数
        if self.seed is not None:
            logger.info(
                "OllamaClient sampling config: model=%s temperature=%s seed=%s num_ctx=%s "
                "(per-call seed = f(base_seed, seed_key or prompt))",
                self.model, self.temperature, self.seed, self.num_ctx,
            )

    def _derive_seed(self, key: str) -> int:
        """base_seed と key（既定は prompt 全文）から決定的にper-callシードを導出。
        並列実行のスケジューリング順に依存しないため、同一 base_seed の再実行で完全再現する。
        """
        h = int(hashlib.sha256(key.encode('utf-8')).hexdigest()[:8], 16)
        return (int(self.seed) * 1000003 + h) % 2147483647

    def _call_seed(self, prompt: str, seed_key: Optional[str] = None) -> int:
        """per-call シードを返す。seed_key を与えると prompt でなく seed_key から導出する。
        文言感度分析（docs/value_provenance.md §4 項目19/21）で、プロンプト文言の差と
        サンプリング乱数の差を分離するために使う（既定＝従来どおり prompt 由来）。"""
        return self._derive_seed(seed_key if seed_key is not None else prompt)

    def generate(
        self,
        prompt: str,
        temperature: float = None,
        max_tokens: int = None,
        seed_key: Optional[str] = None,
    ) -> str:
        """
        Generate text using Ollama API

        Args:
            prompt: Input prompt for the LLM
            temperature: Sampling temperature (uses instance default if None)
            max_tokens: Maximum tokens to generate (uses instance default if None)
            seed_key: Optional key to derive the per-call seed from (defaults to prompt).
                      文言比較の対標本化用。base_seed 未設定時は無視される。

        Returns:
            Generated text response
        """
        # Use instance defaults if not specified
        if temperature is None:
            temperature = self.temperature
        if max_tokens is None:
            max_tokens = self.max_tokens

        self.call_count += 1
        options = {
            "temperature": temperature,
            "num_predict": max_tokens,
            "repeat_penalty": self.repeat_penalty,
            "repeat_last_n": self.repeat_last_n,
            "min_p": self.min_p
        }
        if self.seed is not None:
            options["seed"] = self._call_seed(prompt, seed_key)
        if self.num_ctx is not None:
            options["num_ctx"] = int(self.num_ctx)
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": options
        }

        # P1-B: ネットワーク失敗は指数バックオフでリトライ（同一 seed のため成功時の再現性は不変）。
        # 使い切ったら failure_count を増やして "" を返す（＝サイレントに劣化させず run_meta に残す）。
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(self.api_url, json=payload, timeout=API_TIMEOUT)
                response.raise_for_status()
                text = response.json().get("response", "").strip()
                if not text:
                    # 接続成功だが空応答（モデルが何も返さない）。リトライは同 seed で無意味なので
                    # しないが、サイレントにせずカウント＋警告する。
                    self.empty_response_count += 1
                    logger.warning("Ollama returned empty response (call #%d)", self.call_count)
                return text
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries:
                    delay = self.retry_backoff * (2 ** attempt)
                    logger.warning("Ollama API error (attempt %d/%d): %s — retrying in %.1fs",
                                   attempt + 1, self.max_retries + 1, e, delay)
                    time.sleep(delay)
                    continue
                self.failure_count += 1
                logger.error("Ollama API failed after %d attempts: %s (total failures=%d)",
                             self.max_retries + 1, e, self.failure_count)
                return ""
            except Exception as e:
                # 非ネットワーク例外（JSON パース等）はリトライせず失敗として記録。
                self.failure_count += 1
                logger.error("Unexpected error in Ollama client: %s (total failures=%d)",
                             e, self.failure_count)
                return ""
        return ""  # 到達しないが安全弁
    
    def check_connection(self) -> bool:
        """Check if Ollama server is accessible"""
        try:
            response = requests.get(
                f"{self.base_url}/api/tags", 
                timeout=CONNECTION_CHECK_TIMEOUT
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def list_models(self) -> List[str]:
        """List all available models in Ollama"""
        try:
            response = requests.get(
                f"{self.base_url}/api/tags", 
                timeout=CONNECTION_CHECK_TIMEOUT
            )
            response.raise_for_status()
            data = response.json()
            models = [model['name'] for model in data.get('models', [])]
            return models
        except Exception as e:
            logger.error(f"Error listing models: {e}")
            return []
    
    def check_model_exists(self) -> bool:
        """Check if the specified model exists"""
        available_models = self.list_models()
        return self.model in available_models

    @staticmethod
    def _match_digest(models: List[Dict], model: str) -> Optional[str]:
        """/api/tags の一覧から model に対応する digest を返す純関数（テスト対象）。
        完全一致を優先。タグ省略指定（例 "llama3.1"）は Ollama 側一覧が常にタグ付き
        （"llama3.1:latest" 等）のため、":latest" → 同ベース名の順でフォールバック照合。"""
        by_name = {str(m.get('name', '')): m.get('digest') for m in models}
        if model in by_name:
            return by_name[model]
        if ':' not in model:
            latest = f"{model}:latest"
            if latest in by_name:
                return by_name[latest]
            for name, digest in by_name.items():
                if name.split(':')[0] == model:
                    return digest
        return None

    def model_digest(self) -> Optional[str]:
        """モデルの digest をベストエフォートで返す（run_meta の再現性記録用）。
        Ollama 未起動・モデル未取得なら None（run_id 署名には使わない＝環境依存のため）。"""
        try:
            response = requests.get(
                f"{self.base_url}/api/tags",
                timeout=CONNECTION_CHECK_TIMEOUT
            )
            response.raise_for_status()
            return self._match_digest(response.json().get('models', []), self.model)
        except Exception:
            pass
        return None

