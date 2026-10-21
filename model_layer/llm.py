"""Language models behind one interface (plan: "pluggable, provider-agnostic model layer").

The application builds the whole prompt; adapters only carry it to a provider and back.
Every adapter has a timeout and an output limit, and turns any provider failure into
``LanguageModelError`` so callers answer 503 instead of leaking provider details.

- ``OllamaModel``: a local development model (Ollama on this machine). SIMULATION of the
  production setup in quality and speed, not in data handling: nothing leaves the laptop.
- ``BedrockClaudeModel``: the production default, Claude via AWS Bedrock Mumbai. Built and
  contract-tested against a stubbed AWS client; **never run live** until the AWS account
  exists (Phase 0).

This module must not import anything from ``lekha``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


@dataclass(frozen=True, slots=True)
class LlmRequest:
    system: str
    user: str
    max_output_tokens: int
    json_schema: dict[str, Any] | None = None  # Any: a JSON Schema document


@dataclass(frozen=True, slots=True)
class LlmResponse:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None


class LanguageModelError(RuntimeError):
    """The model could not be reached or returned something unusable."""


class LanguageModel(Protocol):
    model_name: str

    async def generate(self, request: LlmRequest) -> LlmResponse: ...

    async def close(self) -> None: ...


class OllamaModel:
    """Local model through Ollama's chat API (http://127.0.0.1:11434 by default)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,  # tests inject a fake server
    ) -> None:
        self.model_name = f"ollama:{model}"
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds, connect=3.0),
            transport=transport,
        )

    async def generate(self, request: LlmRequest) -> LlmResponse:
        body: dict[str, Any] = {  # Any: JSON request body
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "stream": False,
            "think": False,  # answer directly; hidden reasoning only costs time locally
            "keep_alive": "30m",  # stay loaded between questions (reloading takes ~30 s)
            "options": {
                "temperature": 0,
                "num_predict": request.max_output_tokens,
                "num_ctx": 8192,
            },
        }
        if request.json_schema is not None:
            body["format"] = request.json_schema
        try:
            response = await self._client.post("/api/chat", json=body)
            response.raise_for_status()
            data = response.json()
            text = data["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise LanguageModelError(f"local model request failed: {type(exc).__name__}") from None
        return LlmResponse(
            text=str(text),
            model=self.model_name,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
        )

    async def close(self) -> None:
        await self._client.aclose()


class BedrockClaudeModel:
    """Claude via the Bedrock Converse API. ``client`` is a boto3 ``bedrock-runtime`` client
    (injected, so tests can stub it). Calls run in a thread because boto3 blocks."""

    def __init__(
        self, client: Any, model_id: str, *, timeout_seconds: float
    ) -> None:  # Any: boto3 client
        self.model_name = f"bedrock:{model_id}"
        self._client = client
        self._model_id = model_id
        self._timeout = timeout_seconds

    @classmethod
    def connect(cls, region: str, model_id: str, *, timeout_seconds: float) -> BedrockClaudeModel:
        import boto3  # only needed when Bedrock is configured
        from botocore.config import Config

        config = Config(
            region_name=region,
            read_timeout=timeout_seconds,
            connect_timeout=5,
            retries={"max_attempts": 2, "mode": "standard"},
        )
        return cls(
            boto3.client("bedrock-runtime", config=config),
            model_id,
            timeout_seconds=timeout_seconds,
        )

    async def generate(self, request: LlmRequest) -> LlmResponse:
        from botocore.exceptions import BotoCoreError, ClientError

        def call() -> Any:  # Any: boto3 response dict
            return self._client.converse(
                modelId=self._model_id,
                system=[{"text": request.system}],
                messages=[{"role": "user", "content": [{"text": request.user}]}],
                inferenceConfig={"maxTokens": request.max_output_tokens, "temperature": 0},
            )

        try:
            data = await asyncio.wait_for(asyncio.to_thread(call), timeout=self._timeout)
            text = "".join(part.get("text", "") for part in data["output"]["message"]["content"])
        except (BotoCoreError, ClientError, TimeoutError, KeyError, TypeError) as exc:
            raise LanguageModelError(f"bedrock request failed: {type(exc).__name__}") from None
        usage = data.get("usage", {})
        return LlmResponse(
            text=text,
            model=self.model_name,
            input_tokens=usage.get("inputTokens"),
            output_tokens=usage.get("outputTokens"),
        )

    async def close(self) -> None:
        return None
