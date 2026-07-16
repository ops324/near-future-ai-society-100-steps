"""
Ollama API client for LLM agent communication
"""
import requests
import json
import hashlib
import logging
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

        try:
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

            response = requests.post(
                self.api_url,
                json=payload,
                timeout=API_TIMEOUT
            )
            response.raise_for_status()
            
            result = response.json()
            return result.get("response", "").strip()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error calling Ollama API: {e}")
            return ""
        except Exception as e:
            logger.error(f"Unexpected error in Ollama client: {e}")
            return ""
    
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

