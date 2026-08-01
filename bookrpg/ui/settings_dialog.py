"""设置对话框：模型供应商 / API Key / 模型 / 字号 / 字体。

- 供应商预设（providers.PROVIDERS）：选中自动填 base_url 与已知模型列表
- 「获取模型」按钮：用当前 Key 调 GET /models 拉取真实模型列表（可手输兜底）
- saved 信号：点确定保存成功后发射（带 font_size），主窗口据此即时重设主题
"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QLineEdit, QMessageBox, QPushButton)

from bookrpg import config, llm
from bookrpg.providers import FONT_CHOICES, PROVIDERS


class SettingsDialog(QDialog):
    saved = Signal(int)  # 保存成功 → font_size

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(460)
        cfg = config.load()

        form = QFormLayout(self)

        # ---- 模型供应商 ----
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(list(PROVIDERS))
        if cfg.get("provider") in PROVIDERS:
            self.provider_combo.setCurrentText(cfg["provider"])
        self.provider_combo.currentTextChanged.connect(self._on_provider_change)
        form.addRow("供应商", self.provider_combo)

        self.base_edit = QLineEdit(cfg["base_url"])
        form.addRow("Base URL", self.base_edit)

        # ---- 模型（可编辑下拉：预设 + 手输 + 获取） ----
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self._fill_models(PROVIDERS.get(cfg.get("provider"), {}).get("models", []),
                          current=cfg.get("model", ""))
        form.addRow("模型", self.model_combo)

        row = QHBoxLayout()
        self.fetch_btn = QPushButton("获取模型")
        self.fetch_btn.clicked.connect(self._fetch_models)
        row.addWidget(self.fetch_btn)
        row.addStretch(1)
        form.addRow(row)

        self.key_edit = QLineEdit(cfg["api_key"])
        self.key_edit.setEchoMode(QLineEdit.Password)
        form.addRow("API Key", self.key_edit)

        # ---- 字号 / 字体 ----
        self.size_combo = QComboBox()
        for s in (13, 15, 17, 19):
            self.size_combo.addItem(f"{s}px", s)
        cur = int(cfg.get("font_size", 15))
        idx = self.size_combo.findData(cur)
        self.size_combo.setCurrentIndex(idx if idx >= 0 else 1)  # 兜底 15px
        form.addRow("对话字号", self.size_combo)

        self.font_combo = QComboBox()
        self.font_combo.setEditable(True)  # 可手输任意已安装字体
        for label, family in FONT_CHOICES:
            self.font_combo.addItem(label, family)
        fam = cfg.get("font_family") or "Microsoft YaHei UI"
        idx = self.font_combo.findData(fam)
        if idx < 0:
            self.font_combo.addItem(f"{fam}（自定义）", fam)  # 保留未知字体
            idx = self.font_combo.count() - 1
        self.font_combo.setCurrentIndex(idx)
        form.addRow("对话字体", self.font_combo)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    # ---------- 内部 ----------

    def _on_provider_change(self, name: str):
        """切换供应商：自动填 base_url 与模型列表（保留当前模型）。"""
        info = PROVIDERS.get(name)
        if not info:
            return
        self.base_edit.setText(info["base_url"])
        self._fill_models(info.get("models", []), current=self.model_combo.currentText())

    def _fill_models(self, models: list[str], current: str):
        """填充模型下拉；当前模型不在列表时追加并选中。"""
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        items = list(models)
        if current and current not in items:
            items.append(current)
        self.model_combo.addItems(items)
        if current:
            self.model_combo.setCurrentText(current)
        self.model_combo.blockSignals(False)

    def _fetch_models(self):
        """用当前 Key + Base URL 拉取供应商真实模型列表。"""
        cfg = config.load()
        cfg.update(api_key=self.key_edit.text().strip(),
                   base_url=self.base_edit.text().strip() or config.DEFAULTS["base_url"])
        try:
            models = llm.list_models(cfg)
        except RuntimeError as e:
            QMessageBox.warning(self, "获取失败", str(e))
            return
        if not models:
            QMessageBox.information(self, "获取模型", "该供应商未返回任何模型，可手动输入模型 ID。")
            return
        self._fill_models(models, current=self.model_combo.currentText())
        QMessageBox.information(self, "获取模型", f"已获取 {len(models)} 个模型。")

    def accept(self):
        cfg = config.load()
        provider = self.provider_combo.currentText()
        info = PROVIDERS.get(provider)
        # 字体：预设项（显示名 vs 族名）与手输（editable 编辑文本）都要支持——
        # editable 下 setEditText 不改 currentIndex，currentData 仍是旧项的族名，
        # 故用"编辑文本 ≠ 当前项显示文本"判断手输
        edit_text = self.font_combo.currentText().strip()
        idx = self.font_combo.currentIndex()
        item_text = self.font_combo.itemText(idx) if idx >= 0 else ""
        if edit_text and edit_text != item_text:
            fam = edit_text  # 手输字体名
        else:
            fam = (self.font_combo.currentData()
                   or edit_text
                   or config.DEFAULTS["font_family"])
        updates = {
            "provider": provider,
            "base_url": self.base_edit.text().strip() or config.DEFAULTS["base_url"],
            "model": self.model_combo.currentText().strip() or config.DEFAULTS["model"],
            "thinking_mode": info.get("thinking", "none") if info else "none",
            "font_size": int(self.size_combo.currentData()),
            "font_family": fam,
        }
        # 只有用户修改了 Key 字段才写入——Key 通常由 .env 自动探测，
        # 无脑落盘会把探测到的 Key 写进项目文件（打包分发时泄露风险）
        new_key = self.key_edit.text().strip()
        if new_key and new_key != cfg.get("api_key", ""):
            updates["api_key"] = new_key
        config.save(updates)
        self.saved.emit(int(self.size_combo.currentData()))  # 主窗口即时重设主题
        super().accept()
