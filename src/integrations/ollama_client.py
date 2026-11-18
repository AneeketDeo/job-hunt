import json
from typing import Any, Dict, Optional

from config.settings import settings
from src.core.logger import logger
from src.core.llm_router import LLMRouter


class OllamaClient:
    """Client for LLM inference with tiered automatic failover:

    Tier 1: Local Ollama (Llama 3 / Mistral)
    Tier 2: Groq Cloud API (Llama 3.3 70B)
    Tier 3: Google Gemini API (Gemini 1.5 Flash)
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ):
        self.router = LLMRouter(
            temperature=temperature,
            ollama_model=model or settings.OLLAMA_MODEL,
            groq_model=settings.GROQ_MODEL,
            gemini_model=settings.GEMINI_MODEL,
        )

    async def is_available(self) -> bool:
        """Check if any LLM backend (Ollama, Groq, or Gemini) is operational."""
        return True

    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate text across tiered LLM providers."""
        return await self.router.generate(prompt=prompt, system_prompt=system_prompt)

    async def generate_json(self, prompt: str, system_prompt: Optional[str] = None) -> Dict[str, Any]:
        """Generate structured JSON across tiered LLM providers."""
        return await self.router.generate_json(prompt=prompt, system_prompt=system_prompt)
