import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class OpenRouterClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "openrouter/free",
        timeout_seconds: int = 20,
        max_tokens: int = 220,
        system_prompt: str = "Ты голосовой ассистент Джарвис. Отвечай кратко и по делу на русском языке.",
    ):
        self.api_key = (api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"
        self.last_error: Optional[str] = None

    def is_enabled(self) -> bool:
        return bool(self.api_key)

    def get_response(self, user_text: str) -> Optional[str]:
        self.last_error = None
        if not self.is_enabled():
            self.last_error = "Не задан OPENROUTER_API_KEY"
            return None

        prompt = (user_text or "").strip()
        if not prompt:
            self.last_error = "Пустой запрос"
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://jarvis.local",
            "X-Title": "JarvisAssistant",
        }
        models_to_try = [self.model]
        if self.model != "openrouter/free":
            models_to_try.append("openrouter/free")

        try:
            for model_name in models_to_try:
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": self.max_tokens,
                    "temperature": 0.5,
                }

                response = requests.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 200:
                    data = response.json()
                    choices = data.get("choices") or []
                    if not choices:
                        self.last_error = "OpenRouter вернул пустой ответ"
                        continue

                    message = choices[0].get("message") or {}
                    content = (message.get("content") or "").strip()
                    if content:
                        return content
                    self.last_error = "OpenRouter вернул пустой текст"
                    continue

                error_preview = response.text[:300]
                logger.warning("OpenRouter (%s) вернул %s: %s", model_name, response.status_code, error_preview)
                if response.status_code == 402:
                    self.last_error = "У OpenRouter нет кредитов для платной модели; пробую бесплатную"
                elif response.status_code == 429:
                    self.last_error = "OpenRouter временно ограничил запросы; попробуй ещё раз чуть позже"
                else:
                    self.last_error = f"OpenRouter ошибка {response.status_code}"

            return None
        except Exception as error:
            self.last_error = f"Ошибка OpenRouter запроса: {error}"
            logger.warning(self.last_error)
            return None
