from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .client import AndaClient
from .chaohong import ChaoHongClient, ChaoHongQueryService
from .combined import CombinedQueryService
from .errors import CarrierError, ConfigurationError
from .models import TrackingResult
from .parser import enforce_limit, parse_fba_input
from .service import AndaQueryService
from .settings import AppSettings
from .storage import ProjectDatabase, StoredCredentials
from .yitong import CaptchaChallenge, YiTongClient, YiTongQueryService
from .wps import (
    DEFAULT_REDIRECT_URI,
    WpsClient,
    WpsCredentials,
    WpsSheetBinding,
    WpsSyncSummary,
    excel_column_to_index,
    index_to_excel_column,
    perform_local_authorization,
)


def app_data_dir() -> Path:
    """将本机配置放在源码项目或EXE旁边的data目录。"""
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parents[1]
    return root / "data"


class LoginWorker(QThread):
    completed = pyqtSignal(bool, str, str)

    def __init__(self, client: AndaClient, credentials: StoredCredentials):
        super().__init__()
        self.client = client
        self.credentials = credentials

    def run(self) -> None:
        try:
            self.client.login(self.credentials.username, self.credentials.password)
            self.completed.emit(True, "", "登录成功")
        except CarrierError as exc:
            self.completed.emit(False, exc.category, exc.user_message)
        except Exception:
            self.completed.emit(False, "unknown", "登录时发生未预期错误")


class QueryWorker(QThread):
    completed = pyqtSignal(object)

    def __init__(self, service, fbas: list[str]):
        super().__init__()
        self.service = service
        self.fbas = fbas

    def run(self) -> None:
        self.completed.emit(self.service.query_many(self.fbas))


class YiTongCaptchaWorker(QThread):
    completed = pyqtSignal(bool, object, str, str)

    def __init__(self, client: YiTongClient):
        super().__init__()
        self.client = client

    def run(self) -> None:
        try:
            self.completed.emit(True, self.client.fetch_captcha(), "", "")
        except CarrierError as exc:
            self.completed.emit(False, None, exc.category, exc.user_message)
        except Exception:
            self.completed.emit(False, None, "unknown", "获取验证码时发生未预期错误")


class YiTongAuthWorker(QThread):
    completed = pyqtSignal(bool, str, str, str)

    def __init__(
        self,
        client: YiTongClient,
        mode: str,
        credentials: StoredCredentials | None = None,
        code: str = "",
        challenge: CaptchaChallenge | None = None,
    ):
        super().__init__()
        self.client = client
        self.mode = mode
        self.credentials = credentials
        self.code = code
        self.challenge = challenge

    def run(self) -> None:
        try:
            if self.mode == "validate":
                self.client.validate_token()
                token = self.client.token or ""
            else:
                assert self.credentials is not None and self.challenge is not None
                token = self.client.login(
                    self.credentials.username,
                    self.credentials.password,
                    self.code,
                    self.challenge,
                )
            self.completed.emit(True, "", "登录成功", token)
        except CarrierError as exc:
            self.completed.emit(False, exc.category, exc.user_message, "")
        except Exception:
            self.completed.emit(False, "unknown", "登录时发生未预期错误", "")


class WpsConnectWorker(QThread):
    authorization_url = pyqtSignal(str)
    completed = pyqtSignal(bool, str, str, object)

    def __init__(self, client: WpsClient, authorize: bool):
        super().__init__()
        self.client = client
        self.authorize = authorize

    def run(self) -> None:
        try:
            if self.authorize:
                perform_local_authorization(self.client, self.authorization_url.emit)
            binding = self.client.validate_and_bind()
            self.completed.emit(True, "", "已连接并验证 US-FBA", binding)
        except CarrierError as exc:
            self.completed.emit(False, exc.category, exc.user_message, None)
        except Exception:
            self.completed.emit(False, "unknown", "连接 WPS 时发生未预期错误", None)


class WpsSyncWorker(QThread):
    completed = pyqtSignal(bool, str, str, object)

    def __init__(self, client: WpsClient, binding: WpsSheetBinding, results: list[TrackingResult]):
        super().__init__()
        self.client = client
        self.binding = binding
        self.results = results

    def run(self) -> None:
        try:
            # 每次写入前重新读取 active_area。用户可能在连接WPS后继续添加
            # FBA行，不能沿用连接时保存的旧范围。
            self.binding = self.client.validate_and_bind()
            summary = self.client.sync_tracking_results(self.binding, self.results)
            self.completed.emit(True, "", summary.message, summary)
        except CarrierError as exc:
            self.completed.emit(False, exc.category, exc.user_message, None)
        except Exception:
            self.completed.emit(False, "unknown", "更新 WPS 共享表时发生未预期错误", None)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        data_dir = app_data_dir()
        self.settings_path = data_dir / "settings.json"
        self.settings = AppSettings.load(self.settings_path)
        self.database = ProjectDatabase(data_dir / "app.db")
        self.client = AndaClient(retries=self.settings.retries)
        self.chaohong_client = ChaoHongClient(retries=self.settings.retries)
        self.yitong_client = YiTongClient(retries=self.settings.retries)
        self.login_worker: LoginWorker | None = None
        self.yitong_captcha_worker: YiTongCaptchaWorker | None = None
        self.yitong_auth_worker: YiTongAuthWorker | None = None
        self.yitong_challenge: CaptchaChallenge | None = None
        self.wps_client: WpsClient | None = None
        self.wps_binding: WpsSheetBinding | None = None
        self.wps_connect_worker: WpsConnectWorker | None = None
        self.wps_sync_worker: WpsSyncWorker | None = None
        self.query_worker: QueryWorker | None = None
        self._build_ui()
        self._load_accounts_and_auto_login()

    def _build_ui(self) -> None:
        self.setWindowTitle("FBA 物流查询（安达 / 超鸿 / 易通）")
        self.resize(1020, 920)
        root = QWidget()
        layout = QVBoxLayout(root)

        account_group = QGroupBox("安达账号（密码加密保存在项目数据库）")
        account_layout = QFormLayout(account_group)
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("请输入自己的安达账号")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("请输入密码")
        account_layout.addRow("账号", self.username_edit)
        account_layout.addRow("密码", self.password_edit)
        account_buttons = QHBoxLayout()
        self.save_login_button = QPushButton("安全保存并登录")
        self.save_login_button.clicked.connect(self.save_and_login)
        self.delete_account_button = QPushButton("删除已保存账号")
        self.delete_account_button.clicked.connect(self.delete_account)
        self.login_status = QLabel("登录状态：未登录")
        account_buttons.addWidget(self.save_login_button)
        account_buttons.addWidget(self.delete_account_button)
        account_buttons.addWidget(self.login_status, 1)
        account_layout.addRow(account_buttons)
        layout.addWidget(account_group)

        yitong_group = QGroupBox("易通账号（密码和会话令牌加密保存在项目数据库）")
        yitong_layout = QFormLayout(yitong_group)
        self.yitong_username_edit = QLineEdit()
        self.yitong_username_edit.setPlaceholderText("请输入自己的易通账号")
        self.yitong_password_edit = QLineEdit()
        self.yitong_password_edit.setEchoMode(QLineEdit.Password)
        self.yitong_password_edit.setPlaceholderText("请输入密码")
        yitong_layout.addRow("账号", self.yitong_username_edit)
        yitong_layout.addRow("密码", self.yitong_password_edit)
        captcha_row = QHBoxLayout()
        self.yitong_captcha_label = QLabel("正在准备验证码…")
        self.yitong_captcha_label.setAlignment(Qt.AlignCenter)
        self.yitong_captcha_label.setMinimumSize(130, 45)
        self.yitong_captcha_edit = QLineEdit()
        self.yitong_captcha_edit.setPlaceholderText("请输入图片验证码")
        self.yitong_refresh_button = QPushButton("刷新验证码")
        self.yitong_refresh_button.clicked.connect(self.refresh_yitong_captcha)
        captcha_row.addWidget(self.yitong_captcha_label)
        captcha_row.addWidget(self.yitong_captcha_edit)
        captcha_row.addWidget(self.yitong_refresh_button)
        yitong_layout.addRow("验证码", captcha_row)
        yitong_buttons = QHBoxLayout()
        self.yitong_login_button = QPushButton("安全保存并登录")
        self.yitong_login_button.clicked.connect(self.save_and_login_yitong)
        self.yitong_delete_button = QPushButton("删除已保存账号")
        self.yitong_delete_button.clicked.connect(self.delete_yitong_account)
        self.yitong_login_status = QLabel("登录状态：未登录")
        yitong_buttons.addWidget(self.yitong_login_button)
        yitong_buttons.addWidget(self.yitong_delete_button)
        yitong_buttons.addWidget(self.yitong_login_status, 1)
        yitong_layout.addRow(yitong_buttons)
        layout.addWidget(yitong_group)

        wps_group = QGroupBox("WPS共享表（仅更新 US-FBA 的“货代最新路由信息”）")
        wps_layout = QFormLayout(wps_group)
        self.wps_app_id_edit = QLineEdit()
        self.wps_app_id_edit.setPlaceholderText("请输入企业自建应用 APPID")
        self.wps_app_secret_edit = QLineEdit()
        self.wps_app_secret_edit.setEchoMode(QLineEdit.Password)
        self.wps_app_secret_edit.setPlaceholderText("请输入 APPKEY；仅加密保存在本机数据库")
        self.wps_share_url_edit = QLineEdit()
        self.wps_share_url_edit.setPlaceholderText("https://www.kdocs.cn/l/…")
        self.wps_fba_col_edit = QLineEdit()
        self.wps_fba_col_edit.setPlaceholderText("例如 A；仅首次填写，表头文字不影响")
        self.wps_fba_col_edit.setMaximumWidth(260)
        self.wps_route_col_edit = QLineEdit()
        self.wps_route_col_edit.setPlaceholderText("例如 I；仅首次填写，表头文字不影响")
        self.wps_route_col_edit.setMaximumWidth(260)
        wps_layout.addRow("APPID", self.wps_app_id_edit)
        wps_layout.addRow("APPKEY", self.wps_app_secret_edit)
        wps_layout.addRow("共享表链接", self.wps_share_url_edit)
        wps_layout.addRow("FBA号列（首次）", self.wps_fba_col_edit)
        wps_layout.addRow("路由信息列（首次）", self.wps_route_col_edit)
        wps_controls = QHBoxLayout()
        self.wps_connect_button = QPushButton("安全保存并连接 WPS")
        self.wps_connect_button.clicked.connect(self.save_and_connect_wps)
        self.wps_revalidate_button = QPushButton("重新验证共享表")
        self.wps_revalidate_button.clicked.connect(self.revalidate_wps)
        self.wps_status = QLabel("WPS状态：未配置")
        wps_controls.addWidget(self.wps_connect_button)
        wps_controls.addWidget(self.wps_revalidate_button)
        wps_controls.addWidget(self.wps_status, 1)
        wps_layout.addRow(wps_controls)
        layout.addWidget(wps_group)

        input_group = QGroupBox("FBA 查询")
        input_layout = QVBoxLayout(input_group)
        self.fba_input = QTextEdit()
        self.fba_input.setPlaceholderText("粘贴一列或一行 FBA 号；可用顿号、逗号、空格、制表符或换行分隔")
        self.fba_input.setMaximumHeight(120)
        input_layout.addWidget(self.fba_input)
        controls = QHBoxLayout()
        controls.addWidget(QLabel(f"系统单次上限：{self.database.max_query_count()} 个"))
        self.query_button = QPushButton("开始查询")
        self.query_button.clicked.connect(self.start_query)
        controls.addWidget(self.query_button)
        self.input_summary = QLabel("")
        controls.addWidget(self.input_summary, 1)
        input_layout.addLayout(controls)
        layout.addWidget(input_group)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["货代", "FBA", "结果", "最新时间", "最新动态", "错误类型", "说明"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage("就绪")

    def _load_accounts_and_auto_login(self) -> None:
        try:
            credentials = self.database.load_anda_credentials()
        except ConfigurationError as exc:
            self._set_login_status(False, "configuration", exc.user_message)
            credentials = None
        if credentials is None:
            self._set_login_status(False, "configuration", "未配置账号")
        else:
            self.username_edit.setText(credentials.username)
            self.password_edit.clear()
            self._start_login(credentials, "正在自动登录…")
        self._load_yitong_account()
        self._load_wps_settings()

    def _load_wps_settings(self) -> None:
        try:
            credentials = self.database.load_wps_credentials()
            tokens = self.database.load_wps_tokens()
            binding = self.database.load_wps_binding()
        except ConfigurationError as exc:
            self._set_wps_status(False, exc.category, exc.user_message)
            return
        if credentials is None:
            self._set_wps_status(False, "configuration", "请填写共享表链接和 WPS 应用信息")
            return
        self.wps_app_id_edit.setText(credentials.app_id)
        self.wps_share_url_edit.setText(credentials.share_url)
        if credentials.fba_col is not None:
            self.wps_fba_col_edit.setText(index_to_excel_column(credentials.fba_col))
        if credentials.route_col is not None:
            self.wps_route_col_edit.setText(index_to_excel_column(credentials.route_col))
        self.wps_app_secret_edit.clear()
        self.wps_binding = binding
        if tokens is None:
            self._set_wps_status(False, "configuration", "配置已保存，请连接 WPS")
            return
        self.wps_client = WpsClient(
            credentials,
            tokens=tokens,
            token_callback=self.database.save_wps_tokens,
        )
        self._start_wps_connection(self.wps_client, authorize=False, message="正在自动验证…")

    def _load_yitong_account(self) -> None:
        try:
            credentials = self.database.load_credentials("yitong")
            token = self.database.load_session_token("yitong")
        except ConfigurationError as exc:
            self._set_yitong_status(False, "configuration", exc.user_message)
            return
        if credentials:
            self.yitong_username_edit.setText(credentials.username)
        if token:
            self.yitong_client.token = token
            self._start_yitong_auth("validate")
        else:
            self._set_yitong_status(False, "configuration", "请完成验证码登录")
            self.refresh_yitong_captcha()

    def save_and_login(self) -> None:
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        try:
            self.database.save_anda_credentials(username, password)
        except ConfigurationError as exc:
            QMessageBox.critical(self, "保存失败", exc.user_message)
            return
        self.password_edit.clear()
        self._start_login(StoredCredentials(username, password), "正在登录…")

    def delete_account(self) -> None:
        if QMessageBox.question(self, "确认", "删除项目数据库中已保存的安达账号和加密密码？") != QMessageBox.Yes:
            return
        try:
            self.database.delete_anda_credentials()
        except ConfigurationError as exc:
            QMessageBox.critical(self, "删除失败", exc.user_message)
            return
        self.client.token = None
        self.username_edit.clear()
        self.password_edit.clear()
        self._set_login_status(False, "configuration", "未配置账号")

    def _start_login(self, credentials: StoredCredentials, message: str) -> None:
        if self.login_worker and self.login_worker.isRunning():
            return
        self.save_login_button.setEnabled(False)
        self.login_status.setText(f"登录状态：{message}")
        self.login_status.setStyleSheet("color: #b36b00")
        self.login_worker = LoginWorker(self.client, credentials)
        self.login_worker.completed.connect(self._login_finished)
        self.login_worker.start()

    def _login_finished(self, success: bool, category: str, message: str) -> None:
        self.save_login_button.setEnabled(True)
        self._set_login_status(success, category, message)

    def _set_login_status(self, success: bool, category: str, message: str) -> None:
        if success:
            self.login_status.setText("登录状态：已登录")
            self.login_status.setStyleSheet("color: green")
            self.statusBar().showMessage("安达登录成功")
        else:
            label = {
                "network": "网络失败",
                "authentication": "认证失败",
                "response": "响应异常",
                "server": "服务异常",
                "rate_limit": "请求受限",
                "configuration": "未配置",
            }.get(category, "失败")
            self.login_status.setText(f"登录状态：{label} — {message}")
            self.login_status.setStyleSheet("color: #b00020")

    def refresh_yitong_captcha(self) -> None:
        if self.yitong_captcha_worker and self.yitong_captcha_worker.isRunning():
            return
        self.yitong_challenge = None
        self.yitong_captcha_label.setPixmap(QPixmap())
        self.yitong_captcha_label.setText("正在加载…")
        self.yitong_refresh_button.setEnabled(False)
        self.yitong_captcha_worker = YiTongCaptchaWorker(self.yitong_client)
        self.yitong_captcha_worker.completed.connect(self._yitong_captcha_finished)
        self.yitong_captcha_worker.start()

    def _yitong_captcha_finished(
        self, success: bool, challenge: CaptchaChallenge | None, category: str, message: str
    ) -> None:
        self.yitong_refresh_button.setEnabled(True)
        if not success or challenge is None:
            self.yitong_captcha_label.setText("加载失败")
            self._set_yitong_status(False, category, message)
            return
        self.yitong_challenge = challenge
        pixmap = QPixmap()
        if not pixmap.loadFromData(challenge.image_bytes):
            self.yitong_captcha_label.setText("图片无效")
            self._set_yitong_status(False, "response", "验证码图片无法显示")
            return
        self.yitong_captcha_label.setText("")
        self.yitong_captcha_label.setPixmap(
            pixmap.scaled(130, 45, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def save_and_login_yitong(self) -> None:
        username = self.yitong_username_edit.text().strip()
        password = self.yitong_password_edit.text()
        code = self.yitong_captcha_edit.text().strip()
        if self.yitong_challenge is None:
            QMessageBox.warning(self, "验证码未就绪", "请刷新易通验证码后再登录。")
            return
        if not code:
            QMessageBox.warning(self, "缺少验证码", "请输入易通图片中的验证码。")
            return
        try:
            self.database.save_credentials("yitong", username, password)
        except ConfigurationError as exc:
            QMessageBox.critical(self, "保存失败", exc.user_message)
            return
        self.yitong_password_edit.clear()
        self._start_yitong_auth(
            "login", StoredCredentials(username, password), code, self.yitong_challenge
        )

    def _start_yitong_auth(
        self,
        mode: str,
        credentials: StoredCredentials | None = None,
        code: str = "",
        challenge: CaptchaChallenge | None = None,
    ) -> None:
        if self.yitong_auth_worker and self.yitong_auth_worker.isRunning():
            return
        self.yitong_login_button.setEnabled(False)
        self.yitong_login_status.setText("登录状态：正在验证…")
        self.yitong_login_status.setStyleSheet("color: #b36b00")
        self.yitong_auth_worker = YiTongAuthWorker(
            self.yitong_client, mode, credentials, code, challenge
        )
        self.yitong_auth_worker.completed.connect(self._yitong_auth_finished)
        self.yitong_auth_worker.start()

    def _yitong_auth_finished(self, success: bool, category: str, message: str, token: str) -> None:
        self.yitong_login_button.setEnabled(True)
        if success:
            try:
                self.database.save_session_token("yitong", token)
            except ConfigurationError as exc:
                self._set_yitong_status(False, exc.category, exc.user_message)
                return
            self.yitong_captcha_edit.clear()
            self._set_yitong_status(True, "", message)
            return
        if category == "authentication":
            self.yitong_client.token = None
            self.database.delete_session_token("yitong")
            self.refresh_yitong_captcha()
        self._set_yitong_status(False, category, message)

    def _set_yitong_status(self, success: bool, category: str, message: str) -> None:
        if success:
            self.yitong_login_status.setText("登录状态：已登录")
            self.yitong_login_status.setStyleSheet("color: green")
            self.statusBar().showMessage("易通登录成功")
            return
        label = {
            "network": "网络失败",
            "authentication": "认证失败",
            "response": "响应异常",
            "server": "服务异常",
            "rate_limit": "请求受限",
            "configuration": "未配置",
        }.get(category, "失败")
        self.yitong_login_status.setText(f"登录状态：{label} — {message}")
        self.yitong_login_status.setStyleSheet("color: #b00020")

    def delete_yitong_account(self) -> None:
        if QMessageBox.question(self, "确认", "删除项目数据库中已保存的易通账号、加密密码和会话？") != QMessageBox.Yes:
            return
        self.database.delete_credentials("yitong")
        self.database.delete_session_token("yitong")
        self.yitong_client.token = None
        self.yitong_username_edit.clear()
        self.yitong_password_edit.clear()
        self.yitong_captcha_edit.clear()
        self._set_yitong_status(False, "configuration", "未配置账号")
        self.refresh_yitong_captcha()

    def _current_wps_credentials(self) -> WpsCredentials:
        app_id = self.wps_app_id_edit.text().strip()
        share_url = self.wps_share_url_edit.text().strip()
        app_secret = self.wps_app_secret_edit.text()
        if not app_secret:
            saved = self.database.load_wps_credentials()
            if saved and saved.app_id == app_id:
                app_secret = saved.app_secret
        return WpsCredentials(
            app_id=app_id,
            app_secret=app_secret,
            share_url=share_url,
            redirect_uri=DEFAULT_REDIRECT_URI,
            fba_col=excel_column_to_index(self.wps_fba_col_edit.text()),
            route_col=excel_column_to_index(self.wps_route_col_edit.text()),
        )

    def save_and_connect_wps(self) -> None:
        try:
            credentials = self._current_wps_credentials()
            self.database.save_wps_credentials(credentials)
        except ConfigurationError as exc:
            QMessageBox.critical(self, "WPS配置保存失败", exc.user_message)
            return
        self.wps_app_secret_edit.clear()
        self.wps_binding = None
        self.wps_client = WpsClient(
            credentials,
            token_callback=self.database.save_wps_tokens,
        )
        self._start_wps_connection(self.wps_client, authorize=True, message="等待浏览器授权…")

    def revalidate_wps(self) -> None:
        try:
            credentials = self._current_wps_credentials()
            self.database.save_wps_credentials(credentials)
            tokens = self.database.load_wps_tokens()
        except ConfigurationError as exc:
            QMessageBox.critical(self, "WPS配置错误", exc.user_message)
            return
        if tokens is None:
            QMessageBox.information(self, "需要授权", "请先点击“安全保存并连接 WPS”。")
            return
        self.wps_client = WpsClient(
            credentials,
            tokens=tokens,
            token_callback=self.database.save_wps_tokens,
        )
        self._start_wps_connection(self.wps_client, authorize=False, message="正在验证共享表…")

    def _start_wps_connection(self, client: WpsClient, authorize: bool, message: str) -> None:
        if self.wps_connect_worker and self.wps_connect_worker.isRunning():
            return
        self.wps_connect_button.setEnabled(False)
        self.wps_revalidate_button.setEnabled(False)
        self.wps_status.setText(f"WPS状态：{message}")
        self.wps_status.setStyleSheet("color: #b36b00")
        self.wps_connect_worker = WpsConnectWorker(client, authorize)
        self.wps_connect_worker.authorization_url.connect(self._open_wps_authorization)
        self.wps_connect_worker.completed.connect(self._wps_connection_finished)
        self.wps_connect_worker.start()

    def _open_wps_authorization(self, url: str) -> None:
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self, "无法打开浏览器", f"请手动打开以下地址完成授权：\n{url}")

    def _wps_connection_finished(
        self, success: bool, category: str, message: str, binding: WpsSheetBinding | None
    ) -> None:
        self.wps_connect_button.setEnabled(True)
        self.wps_revalidate_button.setEnabled(True)
        if success and binding is not None:
            self.wps_client = self.wps_connect_worker.client if self.wps_connect_worker else self.wps_client
            self.wps_binding = binding
            self.database.save_wps_binding(binding)
            self._set_wps_status(True, "", message)
        else:
            self.wps_binding = None
            self._set_wps_status(False, category, message)

    def _set_wps_status(self, success: bool, category: str, message: str) -> None:
        if success:
            self.wps_status.setText(f"WPS状态：已连接 — {message}")
            self.wps_status.setStyleSheet("color: green")
            return
        label = {
            "network": "网络失败",
            "authentication": "授权或权限失败",
            "response": "接口响应异常",
            "configuration": "未配置",
        }.get(category, "失败")
        self.wps_status.setText(f"WPS状态：{label} — {message}")
        self.wps_status.setStyleSheet("color: #b00020")

    def start_query(self) -> None:
        parsed = parse_fba_input(self.fba_input.toPlainText())
        summary_parts = [f"有效 {len(parsed.valid)} 个"]
        if parsed.duplicates:
            summary_parts.append(f"重复 {len(parsed.duplicates)} 个（已去重）")
        if parsed.invalid:
            summary_parts.append(f"无效 {len(parsed.invalid)} 个")
        self.input_summary.setText("；".join(summary_parts))
        if parsed.invalid:
            preview = "、".join(parsed.invalid[:10])
            suffix = "…" if len(parsed.invalid) > 10 else ""
            QMessageBox.warning(self, "发现无效输入", f"以下内容不会查询：\n{preview}{suffix}")
        if not parsed.valid:
            QMessageBox.warning(self, "没有有效 FBA", "请输入以 FBA 开头的合理编号。")
            return
        try:
            enforce_limit(parsed.valid, self.database.max_query_count())
        except ValueError as exc:
            QMessageBox.warning(self, "超过批量上限", str(exc))
            return
        self.query_button.setEnabled(False)
        self.table.setRowCount(0)
        self.statusBar().showMessage(f"正在分批查询 {len(parsed.valid)} 个 FBA…")
        anda_service = AndaQueryService(
            self.client,
            batch_size=self.settings.batch_size,
            request_interval=self.settings.request_interval,
        )
        yitong_service = YiTongQueryService(
            self.yitong_client,
            batch_size=self.settings.batch_size,
            request_interval=self.settings.request_interval,
        )
        service = CombinedQueryService(
            anda_service,
            ChaoHongQueryService(self.chaohong_client),
            yitong_service,
        )
        self.query_worker = QueryWorker(service, parsed.valid)
        self.query_worker.completed.connect(self._query_finished)
        self.query_worker.start()

    def _query_finished(self, results: list[TrackingResult]) -> None:
        self.table.setRowCount(len(results))
        for row, result in enumerate(results):
            values = [
                result.carrier,
                result.fba,
                result.status.value,
                result.latest_time,
                result.latest_event,
                result.error_category,
                result.error_message,
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        succeeded = sum(item.status.value == "查询成功" for item in results)
        missing = sum(item.status.value == "未找到" for item in results)
        conflict = sum(item.status.value == "货代冲突" for item in results)
        partial = sum(item.status.value == "部分查询失败" for item in results)
        failed = len(results) - succeeded - missing - conflict - partial
        query_message = (
            f"查询完成：成功 {succeeded}，未找到 {missing}，冲突 {conflict}，部分失败 {partial}，失败 {failed}"
        )
        if self.wps_client and self.wps_binding:
            self.statusBar().showMessage(query_message + "；正在更新 WPS US-FBA…")
            self.wps_sync_worker = WpsSyncWorker(
                self.wps_client, self.wps_binding, results
            )
            self.wps_sync_worker.completed.connect(self._wps_sync_finished)
            self.wps_sync_worker.start()
        else:
            self.query_button.setEnabled(True)
            self.statusBar().showMessage(query_message + "；WPS未连接，未写入共享表")

    def _wps_sync_finished(
        self, success: bool, category: str, message: str, summary: WpsSyncSummary | None
    ) -> None:
        self.query_button.setEnabled(True)
        if success:
            if self.wps_sync_worker is not None:
                self.wps_binding = self.wps_sync_worker.binding
                self.database.save_wps_binding(self.wps_binding)
            self._set_wps_status(True, "", "US-FBA同步完成：" + message)
            self.statusBar().showMessage("物流查询及 WPS 更新完成：" + message)
            if summary and (summary.duplicate_rows or summary.failures):
                details = []
                if summary.duplicate_rows:
                    details.append("重复FBA：" + "、".join(summary.duplicate_rows[:10]))
                if summary.failures:
                    details.append("失败：" + "、".join(summary.failures[:10]))
                QMessageBox.warning(self, "WPS部分项目未更新", "\n".join(details))
            return
        self._set_wps_status(False, category, message)
        self.statusBar().showMessage("物流查询完成，但 WPS 更新失败：" + message)
        QMessageBox.warning(self, "WPS更新失败", message)


def run() -> int:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    return app.exec_()
