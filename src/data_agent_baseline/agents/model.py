from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from openai import APIError, OpenAI

# Temporary debug toggle: Set env var ENABLE_MODEL_DEBUG=1 to enable detailed model logs
ENABLE_MODEL_DEBUG = os.environ.get("ENABLE_MODEL_DEBUG", "0") == "1"

@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelStep:
    thought: str
    action: str
    action_input: dict[str, Any]
    raw_response: str


class ModelAdapter(Protocol):
    def complete(self, messages: list[ModelMessage]) -> str:
        raise NotImplementedError


class OpenAIModelAdapter:
    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str,
        temperature: float,
    ) -> None:
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.temperature = temperature

    def complete(self, messages: list[ModelMessage], max_retries: int = 2) -> str:
        if not self.api_key:
            raise RuntimeError("Missing model API key in config.agent.api_key.")

        client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
        )

        # Create a mutable copy of messages in case we need to append a retry prompt
        current_messages = list(messages)
        last_error_debug_info = ""

        for attempt in range(max_retries + 1):
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": message.role, "content": message.content} for message in current_messages],
                    temperature=self.temperature,
                    timeout=90.0, # 90秒单次调用超时，防止模型卡死
                )
            except APIError as exc:
                raise RuntimeError(f"Model request failed: {exc}") from exc
            except Exception as exc:
                raise RuntimeError(f"Model request encountered an unexpected error: {exc}") from exc

            choices = response.choices or []
            if not choices:
                raise RuntimeError("Model response missing choices.")
                
            choice = choices[0]
            content = choice.message.content
            
            # 调试日志：如果开启了调试模式，打印模型的原始响应特征
            if ENABLE_MODEL_DEBUG:
                logging.info(f"[DEBUG] Attempt {attempt + 1}: Model finish_reason: {choice.finish_reason}")
                logging.info(f"[DEBUG] Attempt {attempt + 1}: Model content type: {type(content)}")
                if content:
                    logging.info(f"[DEBUG] Attempt {attempt + 1}: Model content preview: {content[:200]}...")
                if getattr(choice.message, "tool_calls", None):
                    logging.info(f"[DEBUG] Attempt {attempt + 1}: Model returned {len(choice.message.tool_calls)} tool_calls.")

            if isinstance(content, str) and content.strip():
                # Success
                return content
                
            # If we get here, content is None or empty string
            debug_info = f"finish_reason={choice.finish_reason}, "
            debug_info += f"content_type={type(content)}, "
            if getattr(choice.message, "tool_calls", None):
                debug_info += f"tool_calls={len(choice.message.tool_calls)}, "
            if getattr(choice.message, "refusal", None):
                debug_info += f"refusal={choice.message.refusal}, "
                
            last_error_debug_info = debug_info
            
            if attempt < max_retries:
                if ENABLE_MODEL_DEBUG:
                    logging.warning(f"[DEBUG] Received empty response, retrying ({attempt + 1}/{max_retries})...")
                # Append a system prompt telling the model it messed up and needs to try again
                current_messages.append(ModelMessage(role="assistant", content="")) # Represent the empty turn
                current_messages.append(ModelMessage(
                    role="user", 
                    content="Your last response was completely empty or blocked. You must output exactly ONE valid JSON object inside a ```json ... ``` block. Please try again."
                ))
            
        # If we exhausted retries
        raise RuntimeError(f"Model response missing text content after {max_retries} retries. Debug info: {last_error_debug_info}")


class ScriptedModelAdapter:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def complete(self, messages: list[ModelMessage]) -> str:
        del messages
        if not self._responses:
            raise RuntimeError("No scripted model responses remaining.")
        return self._responses.pop(0)
