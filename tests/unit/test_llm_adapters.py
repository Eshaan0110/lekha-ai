"""Language-model adapters: what they send, and that every failure becomes one error type.

No network: Ollama is replaced by an in-process fake server, and Bedrock by botocore's
Stubber (AWS's own tool for contract-testing a client without calling AWS).
"""

from __future__ import annotations

import json
from typing import Any

import boto3
import httpx
import pytest
from botocore.stub import Stubber

from model_layer import BedrockClaudeModel, LanguageModelError, LlmRequest, OllamaModel

REQUEST = LlmRequest(
    system="Answer only from the sources.",
    user="Question: how many leave days?",
    max_output_tokens=200,
    json_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
)


def _ollama(handler: Any) -> OllamaModel:  # Any: an httpx mock handler
    return OllamaModel(
        "http://127.0.0.1:11434",
        "qwen3.5:9b",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )


async def test_ollama_sends_a_deterministic_structured_local_request() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content), url=str(request.url))
        return httpx.Response(
            200,
            json={
                "message": {"content": '{"answer": "20 days"}'},
                "prompt_eval_count": 31,
                "eval_count": 7,
            },
        )

    model = _ollama(handler)
    response = await model.generate(REQUEST)
    await model.close()
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert seen["messages"] == [
        {"role": "system", "content": REQUEST.system},
        {"role": "user", "content": REQUEST.user},
    ]
    assert seen["format"] == REQUEST.json_schema
    assert seen["stream"] is False
    assert seen["options"]["temperature"] == 0
    assert seen["options"]["num_predict"] == 200
    assert (response.text, response.input_tokens, response.output_tokens) == (
        '{"answer": "20 days"}',
        31,
        7,
    )
    assert response.model == "ollama:qwen3.5:9b"


@pytest.mark.parametrize(
    "handler",
    [
        lambda _: httpx.Response(500, json={"error": "boom"}),
        lambda _: httpx.Response(200, content=b"not json"),
        lambda _: httpx.Response(200, json={"unexpected": True}),
    ],
)
async def test_ollama_failures_become_one_error(handler: Any) -> None:
    model = _ollama(handler)
    with pytest.raises(LanguageModelError):
        await model.generate(REQUEST)
    await model.close()


async def test_ollama_being_down_is_an_error_not_a_crash() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    model = _ollama(refuse)
    with pytest.raises(LanguageModelError, match="ConnectError"):
        await model.generate(REQUEST)
    await model.close()


def _bedrock() -> tuple[BedrockClaudeModel, Stubber]:
    client = boto3.client(
        "bedrock-runtime",
        region_name="ap-south-1",
        aws_access_key_id="testing-only",
        aws_secret_access_key="testing-only",
    )
    return BedrockClaudeModel(client, "test-claude-model", timeout_seconds=5), Stubber(client)


async def test_bedrock_contract_matches_the_converse_api() -> None:
    model, stubber = _bedrock()
    stubber.add_response(
        "converse",
        {
            "output": {"message": {"role": "assistant", "content": [{"text": '{"answer": "20"}'}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 40, "outputTokens": 6, "totalTokens": 46},
            "metrics": {"latencyMs": 10},
        },
        expected_params={
            "modelId": "test-claude-model",
            "system": [{"text": REQUEST.system}],
            "messages": [{"role": "user", "content": [{"text": REQUEST.user}]}],
            "inferenceConfig": {"maxTokens": 200, "temperature": 0},
        },
    )
    with stubber:
        response = await model.generate(REQUEST)
    assert (response.text, response.input_tokens, response.output_tokens) == (
        '{"answer": "20"}',
        40,
        6,
    )
    assert response.model == "bedrock:test-claude-model"


async def test_bedrock_errors_become_one_error() -> None:
    model, stubber = _bedrock()
    stubber.add_client_error(
        "converse", service_error_code="ThrottlingException", http_status_code=429
    )
    with stubber, pytest.raises(LanguageModelError, match="bedrock request failed"):
        await model.generate(REQUEST)
