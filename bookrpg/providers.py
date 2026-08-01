"""模型供应商预设：OpenAI 兼容端点 + 模型列表 + thinking 快慢适配。

- 所有供应商走 OpenAI 兼容协议（openai SDK 统一调用）；
  Anthropic 官方端点是非兼容协议，故不内置（可通过 OpenRouter 等网关使用）。
- thinking 适配：不同供应商控制"快速/深度思考"的参数不同。
  `THINKING_ADAPTERS[mode](fast: bool) -> dict` 返回要追加到请求的 kwargs：
  - deepseek：extra_body.thinking（官方文档，本项目实测过）
  - openai_effort：reasoning_effort low/high（Chat Completions 官方参数）
  - qwen：extra_body.enable_thinking（阿里云百炼 OpenAI 兼容模式官方参数）
  - 未列出/未知：无适配（保持模型默认行为）
- models 列表仅收录官方文档确认的 ID；OpenAI/Gemini 因版本迭代快、
  未验证的 ID 不内置——用设置里的「获取模型」按钮经 /models 端点拉取，
  或直接手输。

数据来源（2026-08）：
- DeepSeek 官方 /models 文档：deepseek-v4-flash / deepseek-v4-pro
- 阿里云百炼官方帮助页：qwen3.7-max/plus/flash、deepseek-v4-*、kimi/kimi-k3、
  glm-5.2、MiniMax/MiniMax-M3
- Kimi / 智谱 / MiniMax / OpenRouter 官方端点
"""


def _deepseek(fast: bool) -> dict:
    return {"extra_body": {"thinking": {"type": "disabled" if fast else "enabled"}}}


def _openai_effort(fast: bool) -> dict:
    return {"reasoning_effort": "low" if fast else "high"}


def _qwen(fast: bool) -> dict:
    return {"extra_body": {"enable_thinking": not fast}}


THINKING_ADAPTERS = {
    "deepseek": _deepseek,
    "openai_effort": _openai_effort,
    "qwen": _qwen,
}

PROVIDERS = {
    "DeepSeek": {
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "thinking": "deepseek",
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "models": [],  # 模型 ID 用「获取模型」拉取或手输（不内置未验证 ID）
        "thinking": "openai_effort",
    },
    "阿里云百炼（通义千问）": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen3.7-max", "qwen3.7-plus", "qwen3.7-flash",
                   "deepseek-v4-pro", "deepseek-v4-flash",
                   "kimi/kimi-k3", "glm-5.2", "MiniMax/MiniMax-M3"],
        "thinking": "qwen",
    },
    "Kimi（Moonshot）": {
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k3"],
        "thinking": "none",
    },
    "智谱 GLM": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-5.2"],
        "thinking": "none",
    },
    "Google Gemini（OpenAI 兼容）": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": [],
        "thinking": "none",
    },
    "OpenRouter（聚合网关）": {
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["openrouter/auto"],
        "thinking": "none",
    },
}

# 设置对话框可选字体（Windows 系统字体；用户可手输任意已安装字体）
FONT_CHOICES = [
    ("微软雅黑", "Microsoft YaHei UI"),
    ("宋体", "SimSun"),
    ("楷体", "KaiTi"),
    ("黑体", "SimHei"),
    ("等线", "DengXian"),
]
DEFAULT_FONT = "Microsoft YaHei UI"

# 字面补偿（px）：中文字体设计行高/字面率不同（fitz 实测：楷体/宋体/黑体行高 1.0em，
# 雅黑 1.32em、等线 1.04em）。同 px 下小行高字体的可视高度先天偏小。
# 渲染字号 = 用户设定字号 + 补偿值。楷体需 ≥18px 才有基本可读性（书法笔画在低像素下糊）。
FONT_SIZE_OFFSETS = {
    "KaiTi": 5,   # 楷体：书法体字面收拢+行高1.0em，视觉最小（用户实测 +5 合适）
    "SimSun": 3,  # 宋体：行高1.0em 但字面满（印刷方块体），视觉中等
    "SimHei": 2,  # 黑体：行高1.0em 但笔画粗字面满，视觉接近雅黑
    "DengXian": 1,  # 等线：行高1.04em 屏幕字体，视觉略小
    "Microsoft YaHei UI": 0,  # 基准
}


def font_size_offset(font_family: str) -> int:
    """按字体返回字号补偿（未收录字体 → 0）。"""
    return FONT_SIZE_OFFSETS.get(font_family or "", 0)
