from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPlainTextEdit, QPushButton

from ace_scheduler.branding import APP_NAME
from ace_scheduler.diagnostics import ROOT, build_info
from ..components import GlassCard, label, setting_row
from .base import Page


class AboutPage(Page):
    def __init__(self, owner):
        super().__init__()
        info = build_info()
        product = GlassCard("关于 " + APP_NAME)
        self.version = label(f"版本 {info['version']} · {'便携构建' if info['distribution'] == 'packaged' else '源码运行'}", "sectionTitle", True)
        product.body.addWidget(self.version)
        self.build = label("构建 " + info["commit"][:12] + (" · 含未提交改动" if info["dirty"] else ""), "muted", True)
        product.body.addWidget(self.build)
        product.body.addWidget(label("Windows 用户态 CPU 调度工具。每次启动只观察，应用与恢复均需明确操作。", "body", True))
        product.body.addWidget(label("当前提供手动检查更新入口，需要私有仓库访问权限。没有自动更新器，程序尚未签名。", "muted", True))
        links = QHBoxLayout()
        for title, url in (("查看发行版本", "https://github.com/chenlaoc/ace-scheduler/releases"),
                           ("反馈问题", "https://github.com/chenlaoc/ace-scheduler/issues")):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, url=url: self.open_url(QUrl(url)))
            links.addWidget(button)
        links.addStretch()
        product.body.addLayout(links)
        self.body.addWidget(product)
        local = GlassCard("本地数据")
        folder = QPushButton("打开配置目录")
        folder.clicked.connect(lambda: self.open_url(QUrl.fromLocalFile(str(owner.manager.path.parent.resolve()))))
        local.body.addWidget(setting_row("配置与恢复记录", str(owner.manager.path.parent), folder))
        self.body.addWidget(local)
        licenses = GlassCard("第三方许可")
        self.notices = QPlainTextEdit()
        self.notices.setReadOnly(True)
        self.notices.setMinimumHeight(220)
        try:
            self.notices.setPlainText((ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8"))
        except OSError:
            self.notices.setPlainText("许可声明文件不可用；请检查程序目录中的 THIRD_PARTY_NOTICES.md 和 licenses 文件夹。")
        licenses.body.addWidget(self.notices)
        button = QPushButton("打开许可证目录")
        button.clicked.connect(lambda: self.open_url(QUrl.fromLocalFile(str(ROOT / "licenses"))))
        licenses.body.addWidget(button)
        self.body.addWidget(licenses)
        self.body.addStretch()

    def open_url(self, url):
        if not QDesktopServices.openUrl(url):
            QMessageBox.warning(self, "无法打开", "未能打开此入口，请检查默认浏览器或本地文件关联。")
