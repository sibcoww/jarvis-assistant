import logging
import os
from typing import Optional, List, Dict

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

    def _normalize_history(self, history: Optional[List[Dict]]) -> List[Dict[str, str]]:
        """Фильтрует историю, оставляя валидные сообщения."""
        normalized: List[Dict[str, str]] = []
        for item in history or []:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in {"user", "assistant", "system"}:
                continue
            if not isinstance(content, str):
                continue
            content = content.strip()
            if not content:
                continue
            normalized.append({"role": role, "content": content})
        return normalized

    def get_response(self, user_text: str, history: Optional[List[Dict]] = None) -> Optional[str]:
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

        history_messages = self._normalize_history(history)

        try:
            for model_name in models_to_try:
                messages = [{"role": "system", "content": self.system_prompt}]
                if history_messages:
                    messages.extend(history_messages)
                messages.append({"role": "user", "content": prompt})

                logger.debug(
                    "OpenRouter request: model=%s, prompt_len=%d, history=%d, max_tokens=%d, timeout=%s",
                    model_name,
                    len(prompt),
                    len(history_messages),
                    self.max_tokens,
                    self.timeout_seconds,
                )

                payload = {
                    "model": model_name,
                    "messages": messages,
                    "max_tokens": self.max_tokens,
                    "temperature": 0.5,
                }

                response = requests.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                logger.debug("OpenRouter response status=%s, len=%s", response.status_code, len(response.text or ""))
                if response.status_code == 200:
                    data = response.json()
                    choices = data.get("choices") or []
                    if not choices:
                        self.last_error = "OpenRouter вернул пустой ответ"
                        continue

                    message = choices[0].get("message") or {}
                    raw_content = message.get("content")

                    # OpenRouter может прислать контент как строку или как список блоков
                    if isinstance(raw_content, list):
                        parts = []
                        for part in raw_content:
                            if isinstance(part, dict):
                                text_piece = part.get("text") or part.get("content") or ""
                                if isinstance(text_piece, str):
                                    parts.append(text_piece)
                        content = "".join(parts).strip()
                    else:
                        content = (raw_content or "").strip()

                    if content:
                        logger.debug("OpenRouter content preview: %s", content[:200])
                        return content

                    # Пустой ответ — считаем ошибкой и пробуем следующую модель
                    self.last_error = "OpenRouter вернул пустой текст"
                    continue

                error_preview = response.text[:500]
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
            logger.exception(self.last_error)
            return None
