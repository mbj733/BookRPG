"""OpenAI 兼容客户端封装：错误处理、重试、JSON 输出。

- 网络错误/超时：最多重试 3 次，指数退避
- 401/402（Key 无效/余额不足）：立即明确报错，不重试
- chat_json：要求模型输出 JSON，解析失败自动重试
"""
import json
import re
import time

from openai import OpenAI

from . import config
from .providers import THINKING_ADAPTERS


class JSONResponseError(RuntimeError):
    """模型连续返回非 JSON（不是 API 故障）。引擎据此降级为纯文本叙述。"""


def _friendly_error(e: Exception) -> str:
    """把常见 LLM 异常翻译成中文描述（网络/超时/限流），其余保留原样。"""
    try:
        from openai import APIConnectionError, APITimeoutError, RateLimitError
    except ImportError:
        return str(e)
    if isinstance(e, APIConnectionError):
        return "网络连接失败（无法连接 API 服务器），请检查网络后重试。"
    if isinstance(e, APITimeoutError):
        return "API 请求超时，请稍后重试。"
    if isinstance(e, RateLimitError):
        return "请求过于频繁（HTTP 429），请稍后重试。"
    return str(e)


def _client(cfg: dict | None = None) -> OpenAI:
    cfg = cfg or config.load()
    return OpenAI(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"] or None,
        max_retries=0,  # 重试由 chat() 统一管理（SDK 默认 2 次会叠加，断网时等待翻倍）
    )


def chat(messages: list[dict], cfg: dict | None = None, model: str | None = None,
         temperature: float = 0.7, max_tokens: int | None = None, timeout: int = 120,
         response_format: dict | None = None, thinking: bool = True,
         stream_cb=None) -> str:
    """普通对话，返回文本。失败重试 4 次，401/402 不重试。

    可重试：网络错误、超时、429/5xx、空内容（reasoning 模型偶发）。
    不可重试：401/402（Key 无效/余额不足）、其他 4xx（提示词问题）。
    thinking=False 时按供应商适配"快速模式"（DeepSeek 禁思考 extra_body、
    OpenAI reasoning_effort=low、通义 enable_thinking=false；none 不加参数），
    响应更快，适合简单/日常内容。stream_cb：流式回调（每块文本调用一次）。
    """
    cfg = cfg or config.load()
    client = _client(cfg)
    last_err = None
    for attempt in range(4):  # 空内容/网络错误重试 4 次（1s/2s/4s 退避）
        try:
            kwargs = dict(
                model=model or cfg["model"],
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            if response_format is not None:
                kwargs["response_format"] = response_format
            # thinking 快慢适配：按供应商追加对应参数（DeepSeek extra_body、
            # OpenAI reasoning_effort、通义 enable_thinking；none 不加参数）
            mode = cfg.get("thinking_mode") or "none"
            adapter = THINKING_ADAPTERS.get(mode)
            if adapter is not None:
                kwargs.update(adapter(fast=not thinking))
            if stream_cb is not None:
                kwargs["stream"] = True
                parts = []
                for chunk in client.chat.completions.create(**kwargs):
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        parts.append(delta.content)
                        stream_cb(delta.content)
                content = "".join(parts)
            else:
                resp = client.chat.completions.create(**kwargs)
                content = resp.choices[0].message.content or ""
            if not content.strip():
                last_err = "模型返回空内容"
                if attempt < 3:
                    time.sleep(2 ** attempt)
                continue
            return content
        except Exception as e:
            last_err = e
            status = getattr(e, "status_code", None)
            if status in (401, 402):
                raise RuntimeError(
                    f"API Key 无效或余额不足（HTTP {status}）。"
                    f"请检查 API Key 配置（环境变量 DEEPSEEK_API_KEY 或 config.json 的 api_key）。"
                    f"详情: {e}"
                ) from e
            # 429 限流、5xx 服务端错误：可重试
            if status and 400 <= status < 500 and status not in (429,):
                raise RuntimeError(f"API 返回错误（HTTP {status}）: {e}") from e
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"LLM 调用失败（4 次重试后）：{_friendly_error(last_err)}")


def chat_json(messages: list[dict], cfg: dict | None = None, model: str | None = None,
              temperature: float = 0.5, max_tokens: int | None = None, retries: int = 2,
              thinking: bool = True, stream_cb=None) -> dict:
    """要求 JSON 输出；空响应或解析失败自动重试。返回 dict。

    注意：不用 response_format=json_object——reasoning 模型在该模式下推理
    显著变长，长上下文时推理吃光 max_tokens 预算导致空内容（实测 3 次连空）。
    改为"提示词强约束 + 普通输出 + 容错解析"，配合充足 max_tokens。
    thinking=False 时禁用模型思考（更快，适合简单内容）。
    stream_cb：流式回调透传给 chat（重试时每次请求都会回调）。
    """
    last_err = None
    work = [dict(m) for m in messages]  # 拷贝，避免污染调用方列表
    for attempt in range(retries + 1):
        text = chat(work, cfg=cfg, model=model, temperature=temperature,
                    max_tokens=max_tokens, thinking=thinking, stream_cb=stream_cb)
        obj = extract_json(text)
        if obj is not None:
            return obj
        last_err = f"模型返回的不是合法 JSON: {text[:200]!r}"
        if attempt < retries:
            # 追加纠正消息再重试——重复同样的提示词只会得到同样的散文
            work.append({"role": "user",
                         "content": "你刚才的输出不是 JSON。请重新输出：只返回一个 JSON 对象，不要任何其他文字。"})
    raise JSONResponseError(f"模型连续返回非 JSON 内容（{retries + 1} 次尝试后）：{last_err}")


def list_models(cfg: dict | None = None) -> list[str]:
    """获取当前供应商可用模型列表（GET /models）。失败抛 RuntimeError。"""
    cfg = cfg or config.load()
    client = _client(cfg)
    try:
        resp = client.models.list()
        return sorted(m.id for m in resp.data)
    except Exception as e:
        raise RuntimeError(f"获取模型列表失败：{e}") from e


def extract_json(text: str) -> dict | None:
    """容错 JSON 解析：
    1) ```json 代码块
    2) 整段直接解析
    3) 截取第一个 { 到最后一个 } 再解析
    """
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", t, re.S)
        if m:
            t = m.group(1).strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e > s:
        try:
            obj = json.loads(t[s:e + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None
