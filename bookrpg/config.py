"""配置读写：config.json → 环境变量覆盖 → 本机开发环境 .env 自动探测。

优先级（高→低）：
1. 环境变量 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL
2. config.json（源码版：项目根；打包版：exe 旁，用户可编辑）
3. 自动探测：本机开发环境 的 .env 与 auth.json（本机免配置默认；仅源码版）

打包版（PyInstaller frozen）：不探测个人开发环境（本机开发环境 .env / auth.json），
配置读写都在 exe 旁 config.json——用户设置里不会出现本机开发 Key。
"""
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if getattr(sys, "frozen", False):
    # 打包版：__file__ 指向只读临时解压目录，配置必须放 exe 旁
    CONFIG_PATH = Path(sys.executable).resolve().parent / "config.json"
else:
    # 源码版：项目根
    CONFIG_PATH = PROJECT_ROOT / "config.json"

DEFAULTS = {
    "api_key": "",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-v4-flash",
    "font_size": 15,        # 对话区字号（px），设置对话框可调
    "font_family": "Microsoft YaHei UI",  # 对话区字体，设置对话框可调
    "provider": "DeepSeek",  # 模型供应商名称（providers.PROVIDERS 键）
    "thinking_mode": "",  # 快慢思考适配（deepseek/openai_effort/qwen/none）；空 = 按 provider 推导
}


def _load_dotenv(path: Path) -> dict:
    """极简 .env 解析（跳过注释/空行），返回 {KEY: value}。"""
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _local_app_data() -> Path:
    """本机 LOCALAPPDATA 目录（无硬编码用户名——打包产物源码纯净）。"""
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"))


def _auth_detect() -> dict:
    """从 本机开发环境 auth.json 的 credential_pool 探测 deepseek 凭据的 base_url。

    打包版返回空（不读取开发机器的个人凭据）。
    """
    if getattr(sys, "frozen", False):
        return {}
    local = _local_app_data()
    auth = local / "hermes" / "auth.json"
    try:
        d = json.loads(auth.read_text(encoding="utf-8"))
        creds = d.get("credential_pool", {}).get("deepseek", [])
        if creds and creds[0].get("base_url"):
            return {"base_url": creds[0]["base_url"]}
    except Exception:
        pass
    return {}


def _dev_env() -> Path:
    """本机开发环境 .env 路径。打包版返回不存在的路径（不探测个人开发环境）。"""
    if getattr(sys, "frozen", False):
        return Path("__frozen__no_dev_env__")
    return _local_app_data() / "hermes" / ".env"


def load() -> dict:
    """返回最终配置 dict。"""
    cfg = dict(DEFAULTS)

    # 1) 项目 config.json（源码版：项目根；打包版：exe 旁）
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[config] config.json 解析失败，使用默认值: {e}")

    # 2) 环境变量（真实环境变量优先于 .env 文件——.env 只是免配置默认值；
    #    打包版 _dev_env() 指向不存在路径，探测自然落空）
    env = _load_dotenv(_dev_env()) | dict(os.environ)
    if env.get("DEEPSEEK_API_KEY"):
        cfg["api_key"] = env["DEEPSEEK_API_KEY"]
    if env.get("DEEPSEEK_BASE_URL"):
        cfg["base_url"] = env["DEEPSEEK_BASE_URL"]

    # 3) 本机开发环境 auth 探测（仅当 base_url 还是默认值时；打包版返回空）
    if cfg["base_url"] == DEFAULTS["base_url"]:
        det = _auth_detect()
        if det.get("base_url"):
            cfg["base_url"] = det["base_url"]

    cfg["api_key"] = str(cfg.get("api_key", "")).strip().strip('"').strip("'")
    # thinking_mode 兜底：config.json 没存时按 provider 推导（未知供应商 → none）
    if not cfg.get("thinking_mode"):
        from .providers import PROVIDERS
        cfg["thinking_mode"] = PROVIDERS.get(cfg.get("provider", ""), {}).get("thinking", "none")
    return cfg


def save(cfg: dict) -> None:
    """合并写入 config.json（保留未提及字段，避免覆盖丢失）。

    注意：settings 对话框保存时不要把自动探测的 api_key 写进来——
    只有用户显式修改的字段才应落盘（见 settings_dialog.accept）。
    """
    existing = {}
    if CONFIG_PATH.exists():
        try:
            existing = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.update(cfg)
    CONFIG_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2),
                           encoding="utf-8")


def check(cfg: dict) -> None:
    """配置健康检查：Key 缺失时给出明确指引，不崩溃。"""
    if not cfg.get("api_key"):
        print("=" * 50)
        print("错误：未找到 API Key。")
        print("  方式1：把 config.example.json 复制为 config.json 并填入 api_key")
        print("  方式2：设置环境变量 DEEPSEEK_API_KEY")
        print("=" * 50)
        raise SystemExit(1)
