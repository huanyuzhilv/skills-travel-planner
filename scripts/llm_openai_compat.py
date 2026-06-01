"""OpenAI 兼容 Chat Completions 小工具（千问 / DeepSeek / OpenAI）。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def strip_md_fence(text: str) -> str:
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def resolve_llm_http_config() -> tuple[str, str]:
    """返回 ``(api_key, base_url)``；无密钥时 ``("", "")``。优先 ``OPENAI_API_KEY``。"""
    oa = (os.environ.get("OPENAI_API_KEY") or "").strip()
    ds = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if oa:
        base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        return oa, base
    if ds:
        base = (
            (os.environ.get("DEEPSEEK_BASE_URL") or "").strip()
            or (os.environ.get("OPENAI_BASE_URL") or "").strip()
            or "https://api.deepseek.com/v1"
        ).rstrip("/")
        return ds, base
    return "", ""


def default_chat_model_id() -> str:
    oa = (os.environ.get("OPENAI_API_KEY") or "").strip()
    ds = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if oa:
        return (os.environ.get("OPENAI_MODEL") or "").strip() or "gpt-4o-mini"
    if ds:
        return (os.environ.get("DEEPSEEK_MODEL") or os.environ.get("OPENAI_MODEL") or "").strip() or "deepseek-chat"
    return (os.environ.get("OPENAI_MODEL") or "").strip() or "gpt-4o-mini"


def chat_completions_text(
    *,
    system: str,
    user: str,
    model: str | None = None,
    timeout_s: int = 120,
    max_tokens: int = 2500,
    temperature: float = 0.55,
) -> str | None:
    """调用 ``/v1/chat/completions``，返回 assistant 文本；失败返回 None。"""
    api_key, base = resolve_llm_http_config()
    if not api_key:
        return None
    model = (model or default_chat_model_id()).strip()
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    url = f"{base}/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw_json = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:600]
        except Exception:
            detail = ""
        print(f"WARN LLM HTTP {exc.code}: {detail or exc.reason}", flush=True)
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"WARN LLM 请求失败: {exc}", flush=True)
        return None

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        print("WARN LLM 响应非 JSON", flush=True)
        return None
    if isinstance(data.get("error"), dict):
        print(f"WARN LLM API 错误: {data['error']}", flush=True)
        return None
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        print("WARN LLM 响应缺少 choices[0].message.content", flush=True)
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    return strip_md_fence(content)


def chat_completions_json(
    *,
    system: str,
    user: str,
    model: str | None = None,
    timeout_s: int = 120,
    max_tokens: int = 2500,
    temperature: float = 0.45,
) -> dict[str, Any] | None:
    """返回解析后的 JSON 对象；失败返回 None。"""
    text = chat_completions_text(
        system=system,
        user=user,
        model=model,
        timeout_s=timeout_s,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        print("WARN LLM 输出非合法 JSON", flush=True)
        return None
    if not isinstance(parsed, dict):
        print("WARN LLM JSON 根节点须为 object", flush=True)
        return None
    return parsed
