from typing import Any

import httpx


class AIClientError(RuntimeError):
    pass


class AIClient:
    """Small local AI adapter for Anthropic Messages or OpenAI Chat Completions.

    Message content may be text or multimodal blocks. Image bytes are prepared
    by the workflow locally and are never uploaded anywhere except this endpoint.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        protocol: str = "openai",
        timeout: int = 120,
        thinking_effort: str = "",
    ):
        if not base_url or not api_key or not model:
            raise AIClientError("AI base URL, API key and model must be configured locally")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.protocol = protocol.lower()
        self.timeout = timeout
        self.thinking_effort = thinking_effort.strip().lower()

    def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 1600,
    ) -> dict[str, Any]:
        if self.protocol == "anthropic-messages":
            return self._anthropic_chat(messages, temperature, max_tokens)
        if self.protocol in {"openai", "openai-chat-completions"}:
            return self._openai_chat(messages, temperature, max_tokens)
        raise AIClientError(f"Unsupported AI protocol: {self.protocol}")

    def _anthropic_chat(self, messages, temperature, max_tokens):
        url = f"{self.base_url}/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        try:
            response = httpx.post(url, headers=headers, json=body, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise AIClientError(f"AI endpoint unavailable: {exc}") from exc
        if response.status_code == 401:
            raise AIClientError(
                "AI endpoint returned 401. Verify the local API key in the local .env file."
            )
        if response.status_code == 404:
            raise AIClientError(
                "AI endpoint returned 404. Verify AA_AI_PROTOCOL and AA_AI_BASE_URL."
            )
        if response.is_error:
            raise AIClientError(
                f"AI endpoint returned HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise AIClientError("AI endpoint returned invalid JSON") from exc
        blocks = payload.get("content") or []
        text = "\n".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text:
            raise AIClientError(
                f"Anthropic-compatible endpoint returned no text content: {payload}"
            )
        return {"role": "assistant", "content": text, "raw": payload}

    # Long replies (storyboards with extended thinking) can take longer than any sane
    # request timeout. Streaming keeps the connection alive; the timeout then only
    # applies to silence between chunks, with a hard cap on the whole call.
    STREAM_MIN_TOKENS = 4000
    STREAM_IDLE_SECONDS = 240
    STREAM_TOTAL_SECONDS = 1500

    def _openai_stream(self, url, headers, body):
        import json
        import time
        parts: list[str] = []
        started = time.monotonic()
        timeout = httpx.Timeout(self.STREAM_IDLE_SECONDS, connect=30)
        try:
            with httpx.stream("POST", url, headers=headers, json={**body, "stream": True},
                              timeout=timeout) as response:
                if response.status_code in (400, 404, 415, 422, 501):
                    return None  # gateway does not stream: fall back to a normal request
                if response.status_code == 401:
                    raise AIClientError(
                        "AI endpoint returned 401. Verify the local API key in the local .env file.")
                if response.is_error:
                    response.read()
                    raise AIClientError(
                        f"AI endpoint returned HTTP {response.status_code}: {response.text[:500]}")
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    response.read()
                    payload = response.json()
                    message = ((payload.get("choices") or [{}])[0].get("message") or {})
                    if not message.get("content"):
                        raise AIClientError("OpenAI-compatible endpoint returned an empty message")
                    return message
                for line in response.iter_lines():
                    if time.monotonic() - started > self.STREAM_TOTAL_SECONDS:
                        raise AIClientError("AI endpoint unavailable: stream exceeded total time limit")
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if chunk.get("error"):
                        raise AIClientError(f"AI endpoint stream error: {str(chunk['error'])[:300]}")
                    for choice in chunk.get("choices") or []:
                        piece = (choice.get("delta") or {}).get("content")
                        if piece:
                            parts.append(piece)
        except httpx.HTTPError as exc:
            raise AIClientError(f"AI endpoint unavailable: {exc}") from exc
        content = "".join(parts)
        if not content:
            raise AIClientError("OpenAI-compatible endpoint returned an empty message")
        return {"role": "assistant", "content": content}

    def _openai_chat(self, messages, temperature, max_tokens):
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.thinking_effort:
            body["thinking_effort"] = self.thinking_effort
        if max_tokens >= self.STREAM_MIN_TOKENS:
            streamed = self._openai_stream(url, headers, body)
            if streamed is not None:
                return streamed
        try:
            response = httpx.post(url, headers=headers, json=body, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise AIClientError(f"AI endpoint unavailable: {exc}") from exc
        if response.status_code == 401:
            raise AIClientError(
                "AI endpoint returned 401. Verify the local API key in the local .env file."
            )
        if response.status_code == 404:
            raise AIClientError(
                "AI endpoint returned 404. Verify AA_AI_PROTOCOL and AA_AI_BASE_URL."
            )
        if response.is_error:
            raise AIClientError(
                f"AI endpoint returned HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise AIClientError("AI endpoint returned invalid JSON") from exc
        choices = payload.get("choices") or []
        if not choices:
            raise AIClientError(f"AI endpoint returned no choices: {payload}")
        message = choices[0].get("message") or {}
        if not message.get("content"):
            raise AIClientError("OpenAI-compatible endpoint returned an empty message")
        return message
