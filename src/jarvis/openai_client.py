import logging
import os
from typing import Optional, List, Dict

import requests

logger = logging.getLogger(__name__)


class OpenAIClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        timeout_seconds: int = 20,
        max_tokens: int = 220,
        system_prompt: str = (
            "Ты голосовой ассистент Джарвис на компьютере пользователя. "
            "Исполнитель может открывать браузер и сайты по командам. "
            "Не утверждай, что не можешь открывать ссылки или что у тебя нет веб-доступа — у пользователя это делает локальный клиент. "
            "Отвечай кратко и по делу на русском."
        ),
    ):
        self.api_key = (api_key or os.getenv("OPENAI_API_KEY") or "").strip()
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.base_url = "https://api.openai.com/v1/chat/completions"
        self.last_error: Optional[str] = None

    def is_enabled(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _normalize_history(history: Optional[List[Dict]]) -> List[Dict[str, str]]:
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

    def get_response(
        self,
        user_text: str,
        history: Optional[List[Dict]] = None,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.5,
    ) -> Optional[str]:
        self.last_error = None
        if not self.is_enabled():
            self.last_error = "Не задан OPENAI_API_KEY"
            return None

        prompt = (user_text or "").strip()
        if not prompt:
            self.last_error = "Пустой запрос"
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        messages = [{"role": "system", "content": system_prompt or self.system_prompt}]
        messages.extend(self._normalize_history(history))
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens if isinstance(max_tokens, int) and max_tokens > 0 else self.max_tokens,
            "temperature": temperature,
        }

        try:
            response = requests.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as error:
            self.last_error = f"OpenAI таймаут запроса: {error}"
            logger.warning(self.last_error)
            return None
        except requests.RequestException as error:
            self.last_error = f"OpenAI сеть недоступна: {error}"
            logger.warning(self.last_error)
            return None

        if response.status_code == 200:
            try:
                data = response.json()
            except Exception as parse_error:  # noqa: B902
                self.last_error = f"OpenAI не смог распарсить ответ: {parse_error}"
                logger.warning(self.last_error)
                return None

            choices = data.get("choices") or []
            if not choices:
                self.last_error = "OpenAI вернул пустой ответ"
                return None

            message = choices[0].get("message") or {}
            content = (message.get("content") or "").strip()
            if not content:
                self.last_error = "OpenAI вернул пустой текст"
                return None

            self.last_error = None
            return content

        if response.status_code == 401:
            self.last_error = "OpenAI ключ недействителен (401)"
            return None
        if response.status_code == 429:
            self.last_error = "OpenAI временно ограничил запросы (429)"
            return None

        self.last_error = f"OpenAI ошибка {response.status_code}"
        logger.warning("OpenAI status=%s: %s", response.status_code, (response.text or "")[:300])
        return None
