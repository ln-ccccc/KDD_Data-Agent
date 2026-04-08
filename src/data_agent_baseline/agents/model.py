from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Any, Protocol

from openai import APIError, OpenAI

from data_agent_baseline.core.errors import ModelError
from data_agent_baseline.core.types import RetryPolicy


@dataclass(frozen=True, slots=True)
class ModelMessage:
    """
    模型消息，包含模型的角色和内容。
    """
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelStep:
    """
    模型步骤，包含模型的思考、操作、操作输入和原始回复。
    """
    thought: str
    action: str
    action_input: dict[str, Any]
    raw_response: str


class ModelAdapter(Protocol):
    """
    模型适配器协议，定义了模型调用的接口。
    """
    def complete(self, messages: list[ModelMessage]) -> str:
        raise NotImplementedError


class OpenAIModelAdapter(ModelAdapter):
    """
    调用 OpenAI 模型，返回模型回复。
    """
    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str,
        temperature: float,
        seed: int | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        self.seed = seed
        self.retry = (retry or RetryPolicy()).normalized()
    
    # 调用 OpenAI 模型，返回模型回复。
    def complete(self, messages: list[ModelMessage]) -> str:
        if not self.api_key:
            raise ModelError("Missing model API key in config.agent.api_key.")

        client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
        )


        last_error: BaseException | None = None
        for attempt in range(self.retry.max_retries + 1):
            try:
                # NOTE: We intentionally do not pass non-standard parameters to maximize
                # compatibility with OpenAI-compatible gateways.
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": message.role, "content": message.content} for message in messages],
                    temperature=self.temperature,
                )
                last_error = None
                break
            except APIError as exc:
                last_error = exc
            except Exception as exc:  # noqa: BLE001
                # Network / gateway transient errors
                last_error = exc

            if attempt < self.retry.max_retries:
                sleep(self.retry.backoff_seconds * (2**attempt))

        if last_error is not None:
            raise ModelError(f"Model request failed after retries: {last_error}") from last_error

        choices = response.choices or []
        if not choices:
            raise ModelError("Model response missing choices.")
        content = choices[0].message.content
        if not isinstance(content, str):
            raise ModelError("Model response missing text content.")
        return content


class ScriptedModelAdapter(ModelAdapter):
    """
    调用脚本模型，返回脚本模型回复。
    """
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def complete(self, messages: list[ModelMessage]) -> str:
        del messages
        if not self._responses:
            raise RuntimeError("No scripted model responses remaining.")
        return self._responses.pop(0)
