"""兼容 OpenAI 接口风格的模型客户端：云端大模型与私域 vLLM / LMDeploy / Ollama 部署均通过它接入。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx


class ProviderError(RuntimeError):
    pass


@dataclass
class ChatResult:
    content: str
    tool_calls: list[dict]
    tokens_in: int
    tokens_out: int
    latency_ms: int
    reasoning_tokens: int = 0


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str | None, model: str, timeout: float = 120.0, retries: int = 2) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "none"
        self.model = model
        self.timeout = timeout
        self.retries = retries

    def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 16000,
        extra: dict | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        for key, value in (extra or {}).items():
            if isinstance(value, dict) and isinstance(body.get(key), dict):
                body[key] = {**body[key], **value}
            else:
                body[key] = value
        if (body.get("thinking") or {}).get("type") == "enabled":
            body.pop("temperature", None)
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode and not tools:
            body["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        resp = None
        last_error = ""
        for attempt in range(self.retries + 1):
            try:
                resp = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=body,
                    timeout=httpx.Timeout(self.timeout, connect=15.0),
                    # httpx 0.28 解析 NO_PROXY 里的 IPv6 项（[::1]）会抛 InvalidURL，外部模型 endpoint 无需环境代理发现
                    trust_env=False,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                resp = None
            else:
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                else:
                    break
            if attempt < self.retries:
                time.sleep(2.0 * (attempt + 1))
        if resp is None or resp.status_code in (429, 500, 502, 503, 504):
            raise ProviderError(f"模型服务调用失败（已重试 {self.retries} 次）：{last_error}")
        latency = int((time.perf_counter() - started) * 1000)
        if resp.status_code >= 400:
            raise ProviderError(f"模型服务返回 {resp.status_code}：{resp.text[:300]}")
        try:
            data = resp.json()
            message = data["choices"][0]["message"]
            if not isinstance(message, dict):
                raise TypeError("message 不是对象")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(f"模型响应格式异常：{resp.text[:300]}") from exc
        usage = data.get("usage") or {}
        tool_calls = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments")}
            tool_calls.append({"id": call.get("id"), "name": fn.get("name"), "arguments": args})
        return ChatResult(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            latency_ms=latency,
            reasoning_tokens=int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
        )


def parse_json_content(content: str) -> Any:
    content = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.S)
    if fenced:
        content = fenced.group(1).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])
        raise
