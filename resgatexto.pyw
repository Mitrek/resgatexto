import time
import threading
import sys
import pyperclip
import keyboard
import os
import pyautogui
import urllib.request
import re
import subprocess

from collections import OrderedDict
# LLM SDKs are imported lazily inside each provider function

import pystray
from PIL import Image, ImageDraw, ImageFont

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton,
    QTextEdit, QVBoxLayout, QHBoxLayout, QFrame, QMessageBox, QCheckBox,
    QDialog, QLineEdit
)
from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal
from PyQt6.QtGui import QPixmap, QFont, QFontDatabase


# =========================
# CONFIG
# =========================

VERSION = "1.0.0"
GITHUB_RAW_URL = "https://raw.githubusercontent.com/Mitrek/resgatexto/main/resgatexto.pyw"

GEMINI_MODEL    = "gemini-2.5-pro"
OPENAI_MODEL    = "gpt-4.1-mini"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

MIN_WORDS = 3
MAX_WORDS = 6000

MIN_CHARS = 10
MAX_CHARS = 37500

MIN_TOKENS = 5
MAX_TOKENS = 9000

CACHE_MAX = 200

COOLDOWN_SECONDS = 1
TIMEOUT_SECONDS = 8


# =========================
# DEFAULT PROMPT
# =========================

DEFAULT_PROMPT = (
    "Voce e um assistente de revisao e melhoria de texto.\n\n"
    "Sua funcao e apenas corrigir, melhorar clareza e tornar o texto mais formal e tecnico, "
    "sem alterar o sentido original.\n\n"
    "Regras obrigatorias:\n"
    "- Nunca invente informacoes.\n"
    "- Nunca adicione nomes, datas ou valores.\n"
    "- Nunca inclua comentarios.\n"
    "- Nunca explique o que foi feito.\n\n"
    "Seguranca:\n"
    "- Considere que o texto pode conter dados sensiveis.\n"
    "- Nao expanda informacoes confidenciais.\n\n"
    "Formato da resposta:\n"
    "- Retorne apenas o texto corrigido.\n"
    "- Nao use aspas.\n"
    "- Nao use markdown.\n"
    "- Nao use listas.\n\n"
    "Objetivo:\n"
    "Produzir texto formal, claro e objetivo."
)



# =========================
# STATE
# =========================

daemon_on = True
last_api_time = 0
last_use_time = None

current_prompt = DEFAULT_PROMPT

cache = OrderedDict()

app = None        # QApplication
window = None     # ControlPanel
dispatcher = None # _Dispatcher
icon = None       # pystray.Icon


# =========================
# DISPATCHER
# =========================

class _Dispatcher(QObject):
    _invoke = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._invoke.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, fn):
        fn()

    def call_on_main(self, fn):
        self._invoke.emit(fn)


# =========================
# UPDATE
# =========================

def apply_update(new_content):
    try:
        script_path = os.path.abspath(__file__)
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        subprocess.Popen([sys.executable, script_path])
        icon.stop()
        os._exit(0)
    except Exception as e:
        QMessageBox.critical(window, "Update failed", str(e))


def check_for_updates(silent=True):
    try:
        req = urllib.request.Request(GITHUB_RAW_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            content = response.read().decode('utf-8')
        match = re.search(r'^VERSION\s*=\s*["\'](.+?)["\']', content, re.MULTILINE)
        if not match:
            return
        remote_version = match.group(1)
        if remote_version == VERSION:
            if not silent:
                def show_current():
                    QMessageBox.information(window, "Sem atualizações",
                        f"Você já está na versão mais recente ({VERSION}).")
                dispatcher.call_on_main(show_current)
            return

        def prompt():
            result = QMessageBox.question(
                window, "Atualização disponível",
                f"Versão {remote_version} disponível (atual: {VERSION}).\nAtualizar agora?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if result == QMessageBox.StandardButton.Yes:
                apply_update(content)

        dispatcher.call_on_main(prompt)

    except Exception:
        if not silent:
            def show_fail():
                QMessageBox.warning(window, "Falha na verificação",
                    "Não foi possível acessar o GitHub. Verifique sua conexão.")
            dispatcher.call_on_main(show_fail)


def update_check_thread():
    time.sleep(5)
    check_for_updates(silent=True)


# =========================
# STARTUP SHORTCUT
# =========================

def _shortcut_path():
    startup = os.path.join(os.environ['APPDATA'],
                           'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')
    return os.path.join(startup, 'Resgatexto.lnk')


def startup_shortcut_exists():
    return os.path.exists(_shortcut_path())


def create_startup_shortcut():
    script = os.path.abspath(__file__)
    lnk = _shortcut_path()
    ps = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        f"$s.TargetPath='pythonw.exe';"
        f"$s.Arguments='\"{script}\"';"
        f"$s.WorkingDirectory='{os.path.dirname(script)}';"
        f"$s.Description='Resgatexto';"
        f"$s.Save()"
    )
    subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True)


def delete_startup_shortcut():
    lnk = _shortcut_path()
    if os.path.exists(lnk):
        os.remove(lnk)


# =========================
# CACHE
# =========================

def cache_get(text):
    if text in cache:
        v = cache.pop(text)
        cache[text] = v
        return v
    return None


def cache_put(raw, enriched):
    cache[raw] = enriched
    cache[enriched] = enriched
    while len(cache) > CACHE_MAX:
        cache.popitem(last=False)


def cache_clear():
    cache.clear()


def cache_size():
    return len(cache)


# =========================
# VALIDATION
# =========================

def count_words(t):
    return len(t.split())


def estimate_tokens(t):
    return len(t) // 4


def should_process(text):

    global last_api_time

    if not daemon_on:
        return False

    if not text:
        return False

    w = count_words(text)
    c = len(text)
    t = estimate_tokens(text)

    if w < MIN_WORDS or w > MAX_WORDS:
        return False

    if c < MIN_CHARS or c > MAX_CHARS:
        return False

    if t < MIN_TOKENS or t > MAX_TOKENS:
        return False

    if time.time() - last_api_time < COOLDOWN_SECONDS:
        return False

    return True


# =========================
# LLM PROVIDERS
# =========================

def _call_gemini(text, output_cap, api_key):
    import google.generativeai as genai
    print(f"[resgatexto] calling Gemini ({GEMINI_MODEL})")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=current_prompt,
    )
    response = model.generate_content(
        text,
        generation_config=genai.types.GenerationConfig(max_output_tokens=output_cap),
        request_options={"timeout": TIMEOUT_SECONDS},
    )
    return response.text.strip()


def _call_openai(text, output_cap, api_key):
    from openai import OpenAI
    print(f"[resgatexto] calling OpenAI ({OPENAI_MODEL})")
    c = OpenAI(api_key=api_key)
    r = c.responses.create(
        model=OPENAI_MODEL,
        timeout=TIMEOUT_SECONDS,
        max_output_tokens=output_cap,
        input=[
            {"role": "system", "content": current_prompt},
            {"role": "user", "content": text},
        ],
    )
    return r.output_text.strip()


def _call_anthropic(text, output_cap, api_key):
    import anthropic
    print(f"[resgatexto] calling Anthropic ({ANTHROPIC_MODEL})")
    c = anthropic.Anthropic(api_key=api_key)
    msg = c.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=output_cap,
        system=current_prompt,
        messages=[{"role": "user", "content": text}],
        timeout=TIMEOUT_SECONDS,
    )
    return msg.content[0].text.strip()


def enrich_text(text):

    global last_api_time
    global last_use_time

    cached = cache_get(text)
    if cached:
        print(f"[resgatexto] cache hit ({len(text)} chars)")
        return cached

    input_tokens = estimate_tokens(text)
    output_cap = min(4096, max(256, input_tokens * 2))
    print(f"[resgatexto] processing text ({len(text)} chars, ~{input_tokens} tokens)")

    providers = [
        ("GEMINI_API_KEY",    _call_gemini),
        ("OPENAI_API_KEY",    _call_openai),
        ("ANTHROPIC_API_KEY", _call_anthropic),
    ]

    out = None
    for env_key, call_fn in providers:
        api_key = os.environ.get(env_key)
        if not api_key:
            print(f"[resgatexto] skipping {env_key} (not set)")
            continue
        try:
            out = call_fn(text, output_cap, api_key)
            if out:
                print(f"[resgatexto] done ({len(out)} chars)")
                break
        except Exception as e:
            print(f"[resgatexto] {env_key} error: {e}")
            continue

    if not out:
        print("[resgatexto] all providers failed or no keys set — returning original")
        return text

    cache_put(text, out)

    last_api_time = time.time()
    last_use_time = time.strftime("%H:%M:%S")

    return out


# =========================
# HOTKEYS
# =========================

def process(paste=False):

    time.sleep(0.08)               # let the hotkey key-up event fire before sending Ctrl+C

    previous = pyperclip.paste()   # save what's on clipboard

    pyautogui.hotkey('ctrl', 'c')  # auto-copy selection
    _deadline = time.perf_counter() + 0.15
    text = previous
    while time.perf_counter() < _deadline:
        time.sleep(0.010)
        text = pyperclip.paste()
        if text != previous:
            break

    if text == previous:           # nothing new was copied (no selection)
        print("[resgatexto] F8 triggered but no text selected")
        return

    if not should_process(text):
        print(f"[resgatexto] skipped ({len(text)} chars, {count_words(text)} words) — validation or cooldown")
        return

    new = enrich_text(text)

    pyperclip.copy(new)

    if paste:
        keyboard.send("ctrl+v")


keyboard.add_hotkey("F8", lambda: threading.Thread(target=lambda: process(True), daemon=True).start())


# =========================
# STYLESHEET
# =========================

def _ensure_checkmark_svg():
    resources_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources')
    path = os.path.join(resources_dir, 'check.svg')
    if not os.path.exists(path):
        os.makedirs(resources_dir, exist_ok=True)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12">'
               '<polyline points="2,6 5,9 10,3" stroke="white" stroke-width="2" '
               'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(svg)
    return path.replace('\\', '/')


_QSS_TEMPLATE = """
QWidget {
    background-color: #EDEAE6;
    color: #3E4347;
    font-family: "Segoe UI";
    font-size: 10pt;
    border: none;
}

QFrame#titleBar {
    background-color: #D9D6D2;
    border-bottom: 1px solid rgba(62, 67, 71, 0.15);
}

QLabel#title {
    background: transparent;
}

QPushButton#closeBtn {
    background-color: transparent;
    color: rgba(62, 67, 71, 0.45);
    border: none;
    border-radius: 4px;
    font-size: 11pt;
    padding: 0px;
}
QPushButton#closeBtn:hover {
    background-color: rgba(220, 50, 50, 0.75);
    color: #ffffff;
}
QPushButton#closeBtn:pressed {
    background-color: rgba(180, 30, 30, 0.9);
}

QLabel#hintLabel {
    font-size: 9pt;
    color: rgba(62, 67, 71, 0.55);
    background: transparent;
    padding: 8px 0px;
}

QLabel#promptLabel {
    font-size: 9px;
    color: rgba(62, 67, 71, 0.50);
    background: transparent;
}

QFrame#promptAccent {
    color: rgba(247, 147, 30, 0.40);
    max-height: 1px;
    background: transparent;
}

QLabel#footer {
    font-size: 11px;
    color: rgba(62, 67, 71, 0.45);
    padding-top: 8px;
    padding-left: 4px;
    padding-right: 4px;
    border-top: 1px solid rgba(62, 67, 71, 0.15);
    background: transparent;
}

QFrame#separator {
    color: rgba(62, 67, 71, 0.10);
    max-height: 1px;
    background: transparent;
}

QTextEdit#promptBox {
    background-color: #E3E0DC;
    color: #3E4347;
    border: 1px solid rgba(62, 67, 71, 0.20);
    border-radius: 6px;
    padding: 8px;
    font-family: "Segoe UI";
    font-size: 9pt;
    selection-background-color: #F7931E;
    selection-color: #3E4347;
}
QTextEdit#promptBox:focus {
    border: 1px solid rgba(247, 147, 30, 0.50);
}

QScrollBar:vertical {
    background: transparent;
    width: 4px;
}
QScrollBar::handle:vertical {
    background: rgba(247, 147, 30, 0.60);
    border-radius: 2px;
    min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QPushButton {
    background-color: transparent;
    color: rgba(62, 67, 71, 0.70);
    border: 1px solid rgba(62, 67, 71, 0.35);
    border-radius: 6px;
    padding: 7px 16px;
    font-size: 9pt;
}
QPushButton:hover {
    border-color: rgba(62, 67, 71, 1.0);
    color: rgba(62, 67, 71, 1.0);
    background-color: transparent;
}
QPushButton:pressed {
    background-color: rgba(62, 67, 71, 0.08);
}

QPushButton#saveBtn {
    background-color: #F7931E;
    color: #4A4F54;
    border: none;
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 600;
    font-size: 9pt;
}
QPushButton#saveBtn:hover {
    background-color: #f9a23c;
    color: #4A4F54;
}
QPushButton#saveBtn:pressed {
    background-color: #e0831a;
}

QPushButton#checkUpdatesBtn {
    background-color: transparent;
    color: rgba(62, 67, 71, 0.55);
    border: 1px solid rgba(62, 67, 71, 0.25);
    border-radius: 6px;
    padding: 0px 16px;
    font-size: 12px;
    min-height: 32px;
    max-height: 32px;
}
QPushButton#checkUpdatesBtn:hover {
    color: rgba(62, 67, 71, 0.70);
    border-color: rgba(62, 67, 71, 0.70);
    background-color: transparent;
}
QPushButton#checkUpdatesBtn:pressed {
    background-color: rgba(62, 67, 71, 0.05);
}

QPushButton#toggleBtn[active="true"] {
    background-color: #F7931E;
    color: #4A4F54;
    border: none;
    border-radius: 4px;
    padding: 2px 10px;
    font-weight: bold;
    font-size: 8pt;
}
QPushButton#toggleBtn[active="true"]:hover {
    background-color: #f9a23c;
}

QPushButton#toggleBtn[active="false"] {
    background-color: transparent;
    color: rgba(62, 67, 71, 0.40);
    border: 1px solid rgba(62, 67, 71, 0.20);
    border-radius: 4px;
    padding: 2px 10px;
    font-weight: bold;
    font-size: 8pt;
}
QPushButton#toggleBtn[active="false"]:hover {
    border-color: rgba(62, 67, 71, 0.40);
    color: rgba(62, 67, 71, 0.60);
}

QCheckBox#startupCheck {
    color: rgba(62, 67, 71, 0.70);
    font-size: 12px;
    spacing: 8px;
    background: transparent;
}
QCheckBox#startupCheck::indicator {
    width: 16px;
    height: 16px;
    border: 1.5px solid rgba(62, 67, 71, 0.40);
    border-radius: 3px;
    background: transparent;
}
QCheckBox#startupCheck::indicator:hover {
    border-color: rgba(62, 67, 71, 0.65);
}
QCheckBox#startupCheck::indicator:checked {
    background-color: #F7931E;
    border-color: #F7931E;
    image: url(CHECKMARK_PATH);
}

QDialog {
    background-color: #EDEAE6;
    border: 1px solid rgba(62, 67, 71, 0.15);
    border-radius: 8px;
}

QLabel#dialogTitle {
    font-size: 11pt;
    font-weight: 600;
    color: #3E4347;
    background: transparent;
}

QLabel#apiName {
    font-size: 10pt;
    font-weight: 600;
    color: #3E4347;
    background: transparent;
}

QLabel#apiStatusOk {
    font-size: 8pt;
    color: #F7931E;
    background: transparent;
}

QLabel#apiStatusMissing {
    font-size: 8pt;
    color: rgba(62, 67, 71, 0.40);
    background: transparent;
}

QLineEdit#apiInput {
    background-color: #E3E0DC;
    color: #3E4347;
    border: 1px solid rgba(62, 67, 71, 0.20);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 9pt;
}
QLineEdit#apiInput:focus {
    border-color: rgba(247, 147, 30, 0.50);
}

QPushButton#apiSaveBtn {
    background-color: #F7931E;
    color: #4A4F54;
    border: none;
    border-radius: 6px;
    padding: 6px 12px;
    font-weight: 600;
    font-size: 9pt;
    min-height: 0px;
}
QPushButton#apiSaveBtn:hover {
    background-color: #f9a23c;
}
QPushButton#apiSaveBtn:pressed {
    background-color: #e0831a;
}

QPushButton#manageApisBtn {
    background-color: transparent;
    color: rgba(62, 67, 71, 0.55);
    border: 1px solid rgba(62, 67, 71, 0.25);
    border-radius: 6px;
    padding: 0px 16px;
    font-size: 12px;
    min-height: 32px;
    max-height: 32px;
}
QPushButton#manageApisBtn:hover {
    color: rgba(62, 67, 71, 0.70);
    border-color: rgba(62, 67, 71, 0.70);
    background-color: transparent;
}
QPushButton#manageApisBtn:pressed {
    background-color: rgba(62, 67, 71, 0.05);
}
"""


def _build_qss():
    return _QSS_TEMPLATE.replace('CHECKMARK_PATH', _ensure_checkmark_svg())


# =========================
# GUI
# =========================

class APIManagerDialog(QDialog):

    APIS = [
        ("Gemini",    "GEMINI_API_KEY"),
        ("OpenAI",    "OPENAI_API_KEY"),
        ("Anthropic", "ANTHROPIC_API_KEY"),
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setFixedWidth(400)
        self._drag_pos = None
        self._build_ui()
        self.adjustSize()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)

        # Title row
        title_row = QHBoxLayout()
        title = QLabel("Gerenciar APIs")
        title.setObjectName("dialogTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.close)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(close_btn)
        layout.addLayout(title_row)

        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        for name, env_key in self.APIS:
            layout.addLayout(self._api_row(name, env_key))

    def _api_row(self, name, env_key):
        row = QVBoxLayout()
        row.setSpacing(6)

        # Name + status
        header = QHBoxLayout()
        name_label = QLabel(name)
        name_label.setObjectName("apiName")
        is_set = bool(os.environ.get(env_key))
        status = QLabel("● configurada" if is_set else "● não configurada")
        status.setObjectName("apiStatusOk" if is_set else "apiStatusMissing")
        header.addWidget(name_label)
        header.addStretch()
        header.addWidget(status)
        row.addLayout(header)

        # Input + save
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        field = QLineEdit()
        field.setObjectName("apiInput")
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText("Cole sua chave aqui..." if not is_set else "Nova chave (opcional)")
        save_btn = QPushButton("Salvar")
        save_btn.setObjectName("apiSaveBtn")
        save_btn.setFixedWidth(72)
        save_btn.clicked.connect(lambda _=False, k=env_key, f=field, s=status: self._save(k, f, s))
        input_row.addWidget(field)
        input_row.addWidget(save_btn)
        row.addLayout(input_row)

        return row

    def _save(self, env_key, field, status_label):
        value = field.text().strip()
        if not value:
            return
        os.environ[env_key] = value
        subprocess.run(['setx', env_key, value], capture_output=True)
        status_label.setText("● configurada")
        status_label.setObjectName("apiStatusOk")
        status_label.style().unpolish(status_label)
        status_label.style().polish(status_label)
        field.clear()
        field.setPlaceholderText("Nova chave (opcional)")


class ControlPanel(QWidget):

    def __init__(self):
        super().__init__()
        self._drag_pos = None
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if event.position().y() < 48:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            else:
                self._drag_pos = None

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _build_ui(self):
        self.setWindowTitle("Resgatexto")
        self.setFixedSize(370, 490)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Title bar ──────────────────────────────
        title_bar = QFrame()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(48)
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(16, 0, 8, 0)
        tb_layout.setSpacing(8)

        font_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', 'Play-Bold.ttf')
        if os.path.exists(font_path):
            font_id = QFontDatabase.addApplicationFont(font_path)
            family = QFontDatabase.applicationFontFamilies(font_id)[0]
        else:
            family = "Play"

        title_label = QLabel()
        title_label.setObjectName("title")
        title_label.setTextFormat(Qt.TextFormat.RichText)
        title_label.setFont(QFont(family, 14, QFont.Weight.Bold))
        title_label.setText('<span style="color:#3E4347;">RESGAT</span><span style="color:#F7931E;">EXTO</span>')

        self._toggle_btn = QPushButton()
        self._toggle_btn.setObjectName("toggleBtn")
        self._toggle_btn.setProperty("active", daemon_on)
        self._toggle_btn.setText("ATIVO" if daemon_on else "DESLIGADO")
        self._toggle_btn.clicked.connect(self._toggle)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(32, 32)
        close_btn.clicked.connect(self.hide)

        tb_layout.addStretch()
        tb_layout.addWidget(title_label)
        tb_layout.addSpacing(8)
        tb_layout.addWidget(self._toggle_btn)
        tb_layout.addStretch()
        tb_layout.addWidget(close_btn)

        main_layout.addWidget(title_bar)

        # ── Content area ───────────────────────────
        content = QFrame()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 16, 24, 16)
        content_layout.setSpacing(8)

        # Usage hint
        hint = QLabel("Selecione um texto em qualquer aplicativo e pressione F8 para corrigir e substituir.")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        content_layout.addWidget(hint)

        # Prompt label + orange accent line
        prompt_label = QLabel("ORIENTAÇÕES À IA")
        prompt_label.setObjectName("promptLabel")
        content_layout.addWidget(prompt_label)

        accent_line = QFrame()
        accent_line.setObjectName("promptAccent")
        accent_line.setFrameShape(QFrame.Shape.HLine)
        content_layout.addWidget(accent_line)

        # Prompt text box
        self._prompt_box = QTextEdit()
        self._prompt_box.setObjectName("promptBox")
        self._prompt_box.setPlainText(DEFAULT_PROMPT)
        content_layout.addWidget(self._prompt_box)

        # Save / Reset buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._save_btn = QPushButton("Salvar prompt")
        self._save_btn.setObjectName("saveBtn")
        self._save_btn.clicked.connect(self._save_prompt)
        reset_btn = QPushButton("Resetar")
        reset_btn.clicked.connect(self._reset_prompt)
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(reset_btn)
        content_layout.addLayout(btn_row)

        # Verificar atualizações + Gerenciar APIs — side by side
        btn_row2 = QHBoxLayout()
        btn_row2.setSpacing(8)
        check_updates_btn = QPushButton("Verificar atualizações")
        check_updates_btn.setObjectName("checkUpdatesBtn")
        check_updates_btn.clicked.connect(
            lambda: threading.Thread(target=lambda: check_for_updates(silent=False), daemon=True).start()
        )
        manage_apis_btn = QPushButton("Gerenciar APIs")
        manage_apis_btn.setObjectName("manageApisBtn")
        manage_apis_btn.clicked.connect(lambda: APIManagerDialog(self).exec())
        btn_row2.addWidget(check_updates_btn)
        btn_row2.addWidget(manage_apis_btn)
        content_layout.addLayout(btn_row2)

        # Startup with Windows toggle
        sep2 = QFrame()
        sep2.setObjectName("separator")
        sep2.setFrameShape(QFrame.Shape.HLine)
        content_layout.addWidget(sep2)

        self._startup_check = QCheckBox("Iniciar automaticamente com o Windows")
        self._startup_check.setObjectName("startupCheck")
        self._startup_check.setChecked(startup_shortcut_exists())
        self._startup_check.toggled.connect(self._toggle_startup)
        content_layout.addWidget(self._startup_check)

        # Footer — last use time only
        self._footer = QLabel()
        self._footer.setObjectName("footer")
        self._footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content_layout.addWidget(self._footer)

        main_layout.addWidget(content)

    def _refresh(self):
        self._footer.setText(f"Último uso: {last_use_time or 'não usado hoje'}")
        active = daemon_on
        self._toggle_btn.setText("ATIVO" if active else "DESLIGADO")
        self._toggle_btn.setProperty("active", active)
        self._toggle_btn.style().unpolish(self._toggle_btn)
        self._toggle_btn.style().polish(self._toggle_btn)

    def _toggle(self):
        global daemon_on
        daemon_on = not daemon_on

    def _save_prompt(self):
        global current_prompt
        current_prompt = self._prompt_box.toPlainText().strip()
        cache_clear()
        self._save_btn.setText("Salvo ✓")
        QTimer.singleShot(1500, lambda: self._save_btn.setText("Salvar prompt"))

    def _reset_prompt(self):
        global current_prompt
        current_prompt = DEFAULT_PROMPT
        self._prompt_box.setPlainText(DEFAULT_PROMPT)
        cache_clear()

    def _toggle_startup(self, checked):
        if checked:
            create_startup_shortcut()
        else:
            delete_startup_shortcut()


def start_gui():
    global app, window, dispatcher

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    dispatcher = _Dispatcher()

    app.setStyleSheet(_build_qss())

    window = ControlPanel()
    # starts hidden — shown only via tray double-click

    app.exec()


# =========================
# TRAY
# =========================

def create_icon():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    grey  = (62, 67, 71, 255)
    orange = (247, 147, 30, 255)
    font = None
    for name in ("arialbd.ttf", "Arial Bold.ttf", "segoeui.ttf", "Segoe UI Bold.ttf"):
        try:
            font = ImageFont.truetype(name, 38)
            break
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()
    r_bbox = d.textbbox((0, 0), "R", font=font)
    t_bbox = d.textbbox((0, 0), "T", font=font)
    r_w = r_bbox[2] - r_bbox[0]
    t_w = t_bbox[2] - t_bbox[0]
    gap = 2
    total_w = r_w + gap + t_w
    text_h = max(r_bbox[3] - r_bbox[1], t_bbox[3] - t_bbox[1])
    r_x = (64 - total_w) // 2 - r_bbox[0]
    t_x = r_x + r_w + gap - t_bbox[0]
    r_y = (64 - text_h) // 2 - r_bbox[1]
    t_y = (64 - text_h) // 2 - t_bbox[1]
    d.text((r_x, r_y), "R", font=font, fill=grey)
    d.text((t_x, t_y), "T", font=font, fill=orange)
    return img


def tray_show(icon, item):
    dispatcher.call_on_main(window.show_and_raise)


def tray_exit(icon, item):
    icon.stop()
    os._exit(0)


def tray_thread():

    global icon

    icon = pystray.Icon(
        "daemon",
        create_icon(),
        "Resgatexto",
        menu=pystray.Menu(
            pystray.MenuItem("Abrir painel", tray_show, default=True),
            pystray.MenuItem("Verificar atualizações", lambda i, item: threading.Thread(target=lambda: check_for_updates(silent=False), daemon=True).start()),
            pystray.MenuItem("Sair", tray_exit),
        ),
    )

    icon.run()


# =========================
# MAIN
# =========================

print("Resgatexto iniciado")

threading.Thread(target=tray_thread, daemon=True).start()
threading.Thread(target=update_check_thread, daemon=True).start()

start_gui()
