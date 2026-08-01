# BookRPG · 书中世界

把一本书变成可扮演的互动游戏：导入电子书（TXT / EPUB / PDF）→ 大模型精读全书 → 生成「世界观包」(.book) → 玩家扮演主角，在书的世界里自由冒险，结局完全开放。

## 玩法

- **导入一本书**：选择 TXT / EPUB / PDF，程序调用 LLM 通读全书，凝练为结构化世界观包（世界设定 / 角色档案 / 规则 / 剧情大纲 / 状态模板）
- **扮演任意角色**：可以扮演原著主角、配角，甚至自捏角色
- **自由推进剧情**：对话式互动，模型驱动叙事；每回合给出 2~4 个方向选项，也可自由输入行动
- **动态状态系统**：生命 / 金钱 / 境界 / 声望……由模型按书定制，随剧情实时变化
- **存档 / 读档**：按书分栏管理，支持删除 / 重命名 / 排序

## 快速开始

```bash
pip install -r requirements.txt
python main.py          # 启动 GUI
python cli.py new ...   # 或使用 CLI（见 cli.py --help）
```

### 配置 API Key

程序需要调用 LLM API（OpenAI 兼容协议），支持 DeepSeek / OpenAI / 阿里云百炼（通义千问）/ Kimi / 智谱 GLM / Gemini / OpenRouter 等供应商。

三种配置方式（优先级从高到低）：

1. **环境变量**：`DEEPSEEK_API_KEY=sk-xxx`（或对应供应商的 Key），可选 `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`
2. **本地 `.env` 文件**（项目根目录，免配置默认值）
3. **GUI 设置对话框**（供应商 / Base URL / 模型 / API Key / 字号 / 字体，保存到 `config.json`）

> 注意：`config.json` 与 `.env` 不会被写入 git（已在 .gitignore 中排除），Key 不会随项目分发。

### 设置

- **模型供应商**：设置对话框内置主流供应商预设（自动填 Base URL 与模型列表），支持「获取模型」按钮从 `/models` 端点拉取真实模型列表，也可手输任意模型 ID
- **快慢思考分层**：日常内容快速响应（禁用/低强度推理），关键抉择（决战 / 抉择 / 背叛……）自动深度思考——按供应商自动适配（DeepSeek `thinking`、OpenAI `reasoning_effort`、通义 `enable_thinking`）
- **字体 / 字号**：微软雅黑 / 宋体 / 楷体 / 黑体 / 等线（可手输任意已安装字体），即时生效；楷体等小行高字体自动补偿字号

## 测试

```bash
python -m unittest discover -s tests    # 78 项，全离线（含录制回放，零 token）
```

- 模型行为用「录制回放」测试：`tests/fixtures/game_session.jsonl` 是真实 API 调用的回放记录（内容为原创测试世界观《星环守望者》），不烧 token 即可回归引擎行为
- 重录回放：`rm tests/fixtures/game_session.jsonl && python tests/record_fixture.py`（烧真实 API ~8 次）
- 提示词 / 引擎行为变更后必须重录，否则回放哈希失配

## 项目结构

```
bookrpg/
  config.py        配置加载（环境变量 > .env > config.json）
  providers.py     供应商预设 / thinking 适配 / 字体补偿表
  llm.py           OpenAI 兼容客户端封装（重试 / JSON 容错 / 模型列表）
  worldbook.py     导入核心：解析 → 并发精读 → 分层聚合 → .book
  engine.py        游戏引擎（回合 / 状态 / 长局历史压缩）
  state.py / save.py / recorder.py
  parser/          EPUB / TXT / PDF 文本提取
  ui/              PySide6 桌面界面（书库 / 游戏页 / 设置）
```

## 数据与隐私

- 所有数据（书库 `.book`、存档、配置）都保存在程序目录的 `books/` 与 `config.json`，不上传任何服务器（除你配置的 LLM API 调用）
- Key 永不写入项目文件（仅环境变量 / .env / config.json 手动填写）
