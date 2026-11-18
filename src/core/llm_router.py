import json
import re
from typing import Any, Dict, List, Optional
import httpx

from config.settings import settings
from src.core.logger import logger


class LLMRouter:
    """Unified Multi-Tier LLM Router with automated failover:

    Tier 1: Local Ollama (Llama 3 / Mistral) - Private & Cost-Free
    Tier 2: Groq Cloud API (Llama 3.3 70B / 8B) - Ultra-fast inference
    Tier 3: Google Gemini API (Gemini 1.5/2.0 Flash) - High-capacity reasoning
    Tier 4: Deterministic Heuristics - Zero-dependency failover
    """

    def __init__(
        self,
        temperature: Optional[float] = None,
        ollama_model: Optional[str] = None,
        groq_model: Optional[str] = None,
        gemini_model: Optional[str] = None,
    ):
        self.temperature = temperature if temperature is not None else settings.OLLAMA_TEMPERATURE
        self.ollama_model = ollama_model or settings.OLLAMA_MODEL
        self.groq_model = groq_model or settings.GROQ_MODEL
        self.gemini_model = gemini_model or settings.GEMINI_MODEL

    # -------------------------------------------------------------------------
    # TIER 1: LOCAL OLLAMA
    # -------------------------------------------------------------------------
    async def _try_ollama(self, prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
        base_url = settings.OLLAMA_BASE_URL.rstrip("/")
        payload: Dict[str, Any] = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        if system_prompt:
            payload["system"] = system_prompt

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                res = await client.post(f"{base_url}/api/generate", json=payload)
                if res.status_code == 200:
                    text = res.json().get("response", "").strip()
                    if text:
                        logger.debug("Successfully generated via [Tier 1: Local Ollama].")
                        return text
        except Exception as e:
            logger.debug(f"Tier 1 (Ollama) attempt failed: {e}")
        return None

    # -------------------------------------------------------------------------
    # TIER 2: GROQ CLOUD API (Llama 3.3)
    # -------------------------------------------------------------------------
    async def _try_groq(self, prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
        api_key = settings.GROQ_API_KEY
        if not api_key or api_key.startswith("gsk_your_") or len(api_key) < 10:
            return None

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Candidate models to try on Groq
        models_to_try = [self.groq_model, "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "allam-2-7b"]
        seen = set()
        unique_models = [m for m in models_to_try if m and not (m in seen or seen.add(m))]

        for model in unique_models:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": 2048,
            }
            try:
                logger.info(f"Ollama unavailable; engaging [Tier 2: Groq Cloud API] ({model})...")
                async with httpx.AsyncClient(timeout=30.0) as client:
                    res = await client.post(url, headers=headers, json=payload)
                    if res.status_code == 200:
                        data = res.json()
                        choices = data.get("choices", [])
                        if choices:
                            text = choices[0].get("message", {}).get("content", "").strip()
                            logger.info(f"Successfully generated response via [Tier 2: Groq] ({model}).")
                            return text
                    elif res.status_code == 404:
                        logger.warning(f"Groq model [{model}] not available. Trying alternate Groq model...")
                        continue
                    else:
                        logger.warning(f"Groq API ({model}) returned HTTP {res.status_code}: {res.text}")
            except Exception as e:
                logger.warning(f"Tier 2 (Groq - {model}) attempt failed: {e}")
        return None

    # -------------------------------------------------------------------------
    # TIER 3: GOOGLE GEMINI API
    # -------------------------------------------------------------------------
    async def _try_gemini(self, prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
        api_key = settings.GEMINI_API_KEY
        if not api_key or api_key.startswith("AIzaSy_your_") or len(api_key) < 10:
            return None

        # Clean model name (e.g. gemini-1.5-flash)
        model = self.gemini_model.replace("models/", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}

        combined_prompt = f"{system_prompt}\n\nUser Request:\n{prompt}" if system_prompt else prompt

        payload = {
            "contents": [
                {
                    "parts": [{"text": combined_prompt}]
                }
            ],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": 2048,
            }
        }

        try:
            logger.info(f"Groq/Ollama unavailable; engaging [Tier 3: Google Gemini API] ({model})...")
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            text = parts[0].get("text", "").strip()
                            logger.info("Successfully generated response via [Tier 3: Gemini].")
                            return text
                else:
                    logger.warning(f"Gemini API returned HTTP {res.status_code}: {res.text}")
        except Exception as e:
            logger.warning(f"Tier 3 (Gemini) attempt failed: {e}")
        return None

    # -------------------------------------------------------------------------
    # UNIFIED EXECUTION PIPELINE
    # -------------------------------------------------------------------------
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Execute query across Tier 1 (Ollama) -> Tier 2 (Groq) -> Tier 3 (Gemini)."""
        # Tier 1: Local Ollama
        text = await self._try_ollama(prompt, system_prompt)
        if text:
            return text

        # Tier 2: Groq
        text = await self._try_groq(prompt, system_prompt)
        if text:
            return text

        # Tier 3: Gemini
        text = await self._try_gemini(prompt, system_prompt)
        if text:
            return text

        logger.warning("All LLM providers (Ollama, Groq, Gemini) are offline or unconfigured.")
        return ""

    async def generate_json(self, prompt: str, system_prompt: Optional[str] = None) -> Any:
        """Generate structured JSON response across providers with resilient parsing."""
        json_sys = (system_prompt or "") + "\nRespond ONLY with a valid JSON. No conversational prelude or markdown codeblocks."
        raw = await self.generate(prompt=prompt, system_prompt=json_sys)
        if not raw:
            return {}

        # 1. Direct parse after stripping codeblocks
        clean_raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        clean_raw = re.sub(r"\s*```$", "", clean_raw).strip()
        try:
            return json.loads(clean_raw)
        except Exception:
            pass

        # 2. Regex search for root array or root object
        match = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", raw)
        if match:
            clean_str = match.group(0)
            try:
                return json.loads(clean_str)
            except Exception as e:
                logger.warning(f"Failed to parse LLM JSON: {e}. Raw: {raw[:200]}")
        return {}
