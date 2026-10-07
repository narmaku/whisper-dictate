#!/usr/bin/env python3
"""Local voice-to-text: record, transcribe, copy, then paste manually."""

import argparse
import logging
import os
import shutil
import subprocess
import sys
import threading

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

__version__ = "0.1.0"
SAMPLE_RATE = 16000
MODELS = ["tiny", "base", "small", "medium", "large-v3", "turbo"]
DEFAULT_MODEL = "small"
LANGUAGES = {
    "Auto": None, "English": "en", "Spanish": "es", "French": "fr",
    "German": "de", "Italian": "it", "Portuguese": "pt", "Russian": "ru",
    "Chinese": "zh", "Japanese": "ja", "Korean": "ko", "Arabic": "ar",
    "Hindi": "hi", "Dutch": "nl", "Polish": "pl", "Turkish": "tr",
    "Ukrainian": "uk", "Czech": "cs", "Swedish": "sv",
}
log = logging.getLogger("dictate")


def load_whisper_model(name):
    """Use downloaded models offline; contact the hub only for a new model."""
    log.info("Loading model %s from the local cache", name)
    try:
        model = WhisperModel(name, device="cpu", compute_type="int8", local_files_only=True)
    except FileNotFoundError:
        log.info("Model %s is not cached; downloading it", name)
        model = WhisperModel(name, device="cpu", compute_type="int8")
    log.info("Model %s is ready", name)
    return model


def clipboard_copy(text):
    """Use the GUI's clipboard without launching external helper windows."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return False
    app.clipboard().setText(text)
    return True


def close_input_stream(stream):
    """Release input immediately; draining PortAudio can hang on this backend."""
    try:
        stream.abort()
    finally:
        stream.close()


def record_audio():
    """Record until Enter; closing the stream also handles Ctrl+C cancellation."""
    chunks = []

    def capture(indata, frames, time_info, status):
        if status:
            log.warning("Audio: %s", status)
        chunks.append(indata.copy())

    print("Recording… Speak, then press Enter to stop. Ctrl+C cancels.", flush=True)
    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                            callback=capture, blocksize=1024)
    try:
        stream.start()
        input()
    finally:
        close_input_stream(stream)
    if not chunks:
        return np.array([], dtype=np.float32)
    return np.concatenate(chunks).flatten()


def transcribe_and_copy(model, audio, language, copy_result=True):
    if not audio.size:
        print("No audio captured. Clipboard unchanged.", flush=True)
        return ""
    peak = np.max(np.abs(audio))
    if peak == 0:
        print("No audio signal captured. Clipboard unchanged.", flush=True)
        return ""
    audio = audio / peak * 0.95
    print("Transcribing…", flush=True)
    segments, _ = model.transcribe(audio, beam_size=5, language=language,
                                  vad_filter=True)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    if not text:
        print("No speech detected. Clipboard unchanged.", flush=True)
        return ""
    if not copy_result:
        return text
    copied = clipboard_copy(text)
    print(text, flush=True)
    if copied:
        print("Copied. Paste normally (Ctrl+Shift+V in terminals).", flush=True)
    else:
        print("Select the transcript above and copy it manually.", flush=True)
    return text


def run_gui(model_name, language, quick=False):
    # Imported only for GUI use, so CLI diagnostics do not require a display.
    from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot, Qt
    from PySide6.QtGui import QKeySequence, QShortcut, QPalette, QColor
    from PySide6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QComboBox, QPushButton, QTextEdit, QProgressBar,
    )

    class Signals(QObject):
        result = Signal(object)
        error = Signal(str)
        finished = Signal(object)

    class Worker(QRunnable):
        def __init__(self, work):
            super().__init__()
            self.work = work
            self.signals = Signals()

        def run(self):
            try:
                self.signals.result.emit(self.work())
            except Exception as exc:
                self.signals.error.emit(str(exc))
            finally:
                self.signals.finished.emit(self)

    class DictationWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Whisper Dictate")
            self.resize(640, 380)
            if quick:
                self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
                self.resize(420, 130)
            self.closing = False
            self.model = None
            self.stream = None
            self.audio_ready = False
            self.audio_busy = False
            self.recording = False
            self.peak_level = 0.0
            self.chunks = []
            self.lock = threading.Lock()
            self.workers = []
            self.quick_pending = quick
            layout = QVBoxLayout(self)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(12)
            selectors = QHBoxLayout()
            selectors.setSpacing(8)
            self.mic = QComboBox()
            self.mic.addItem("Detecting microphones…", None)
            self.mic.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.mic.setMinimumContentsLength(14)
            self.mic.setMinimumWidth(180)
            self.mic.setMaximumWidth(300)
            self.models = QComboBox()
            self.models.addItems(MODELS)
            self.models.setCurrentText(model_name)
            self.languages = QComboBox()
            for name, code in LANGUAGES.items():
                self.languages.addItem(name, code)
            selected = self.languages.findData(language)
            self.languages.setCurrentIndex(max(selected, 0))
            for name, widget in [("Mic:", self.mic), ("Model:", self.models),
                                 ("Language:", self.languages)]:
                label = QLabel(name)
                label.setFixedWidth(label.sizeHint().width())
                selectors.addWidget(label)
                selectors.addWidget(widget)
            selectors.addStretch(1)
            layout.addLayout(selectors)
            self.text = QTextEdit()
            self.text.setPlaceholderText("Your transcription will appear here.")
            layout.addWidget(self.text)
            buttons = QHBoxLayout()
            self.record = QPushButton("Record")
            self.record.setObjectName("recordButton")
            self.record.setProperty("recording", False)
            self.copy = QPushButton("Copy")
            self.clear = QPushButton("Clear")
            for button in [self.record, self.copy, self.clear]:
                buttons.addWidget(button)
            layout.addLayout(buttons)
            self.status = QLabel("Loading model…")
            self.status.setObjectName("statusLabel")
            status_row = QHBoxLayout()
            status_row.addWidget(self.status, 1)
            self.level = QProgressBar()
            self.level.setRange(0, 100)
            self.level.setValue(0)
            self.level.setTextVisible(False)
            self.level.setFixedSize(100, 10)
            status_row.addWidget(self.level)
            layout.addLayout(status_row)
            self.level_timer = QTimer(self)
            self.level_timer.timeout.connect(self.update_level)
            self.level_timer.start(50)
            if quick:
                self.setWindowTitle("Whisper Dictate — Quick")
                for index in range(selectors.count()):
                    widget = selectors.itemAt(index).widget()
                    if widget is not None:
                        widget.hide()
                self.text.hide()
                self.text.setMaximumHeight(90)
                self.copy.hide()
                self.clear.hide()
                self.cancel_shortcut = QShortcut(QKeySequence("Escape"), self)
                self.cancel_shortcut.activated.connect(self.close)
            self.record.clicked.connect(self.toggle_record)
            self.copy.clicked.connect(self.copy_text)
            self.clear.clicked.connect(self.text.clear)
            self.models.currentTextChanged.connect(self.load_model)
            self.work(self.discover_microphones, self.microphones_loaded)
            self.load_model()

        def work(self, function, result):
            worker = Worker(function)
            self.workers.append(worker)
            worker.signals.result.connect(result)
            worker.signals.error.connect(self.failed)
            worker.signals.finished.connect(self.worker_finished)
            QThreadPool.globalInstance().start(worker)

        @Slot(object)
        def worker_finished(self, worker):
            self.workers.remove(worker)
            if self.closing and not self.workers:
                QApplication.instance().quit()

        def controls(self, busy):
            for widget in [self.mic, self.models, self.languages]:
                widget.setEnabled(not busy)
            self.record.setEnabled(not busy and self.model is not None and self.audio_ready)

        def load_model(self, *_):
            self.model = None
            self.controls(True)
            name = self.models.currentText()
            self.status.setText(f"Loading {name} model…")
            self.work(lambda: load_whisper_model(name),
                      self.model_loaded)

        @Slot(object)
        def model_loaded(self, model):
            if self.closing:
                return
            self.model = model
            self.controls(False)
            self.status.setText("Ready. Record speech, then copy and paste normally.")
            self.maybe_start_quick()

        @staticmethod
        def discover_microphones():
            return sd.query_devices(), sd.default.device[0]

        @Slot(object)
        def microphones_loaded(self, result):
            if self.closing:
                return
            devices, default = result
            self.mic.clear()
            for index, device in enumerate(devices):
                if device["max_input_channels"] > 0:
                    self.mic.addItem(device["name"], index)
            selected = self.mic.findData(default)
            if selected >= 0:
                self.mic.setCurrentIndex(selected)
            self.audio_ready = self.mic.count() > 0
            if not self.audio_ready:
                self.failed("No microphone found")
                return
            self.controls(self.model is None)
            self.maybe_start_quick()

        def maybe_start_quick(self):
            if self.quick_pending and self.model is not None and self.audio_ready:
                self.quick_pending = False
                self.toggle_record()

        def update_level(self):
            self.level.setValue(min(int(self.peak_level * 500), 100) if self.recording else 0)

        @Slot(str)
        def failed(self, message):
            if self.closing:
                return
            log.error("GUI operation failed: %s", message)
            self.quick_pending = False
            self.audio_busy = False
            self.recording = False
            self.record.setProperty("recording", False)
            self.record.style().unpolish(self.record)
            self.record.style().polish(self.record)
            self.controls(False)
            self.record.setText("Record")
            self.status.setText(f"Error: {message}")

        def capture(self, indata, frames, time_info, status):
            if status:
                log.warning("Audio: %s", status)
            with self.lock:
                self.chunks.append(indata.copy())
                self.peak_level = float(np.max(np.abs(indata)))

        def toggle_record(self):
            if self.audio_busy:
                return
            self.audio_busy = True
            self.controls(True)
            if self.stream is None:
                self.chunks = []
                device = self.mic.currentData()
                self.status.setText("Opening microphone…")
                log.info("Opening microphone device %s", device)

                def start_stream():
                    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                            dtype="float32", device=device,
                                            callback=self.capture, blocksize=1024)
                    try:
                        stream.start()
                    except Exception:
                        stream.close()
                        raise
                    return stream

                self.work(start_stream, self.recording_started)
                return
            stream, self.stream = self.stream, None
            self.recording = False
            self.record.setText("Record")
            self.record.setProperty("recording", False)
            self.record.style().unpolish(self.record)
            self.record.style().polish(self.record)
            self.status.setText("Stopping microphone…")
            log.info("Stopping microphone and transcribing")
            lang = self.languages.currentData()
            model = self.model

            def stop_and_transcribe():
                close_input_stream(stream)
                with self.lock:
                    audio = np.concatenate(self.chunks).flatten() if self.chunks else np.array([])
                    self.chunks = []
                return transcribe_and_copy(model, audio, lang, copy_result=False)

            self.work(stop_and_transcribe, self.transcribed)

        @Slot(object)
        def recording_started(self, stream):
            if self.closing:
                self.work(lambda: close_input_stream(stream), lambda _: None)
                return
            self.stream = stream
            self.audio_busy = False
            self.recording = True
            self.record.setEnabled(True)
            self.record.setText("Stop")
            self.record.setProperty("recording", True)
            self.record.style().unpolish(self.record)
            self.record.style().polish(self.record)
            self.status.setText("Recording… Click Stop when finished.")
            log.info("Microphone recording started")

        @Slot(object)
        def transcribed(self, text):
            if self.closing:
                return
            self.audio_busy = False
            self.controls(False)
            log.info("Transcription completed (%s characters)", len(text))
            if not text:
                self.status.setText("No speech detected. Clipboard unchanged.")
                return
            previous = self.text.toPlainText().strip()
            self.text.setPlainText((previous + " " + text).strip())
            if quick:
                self.text.show()
            copied = clipboard_copy(text)
            self.status.setText("Copied. Paste normally." if copied else
                                "Copy failed. Select and copy the transcript manually.")
            if quick and copied:
                self.status.setText("Copied. Paste in your app, then press Escape to close.")

        def copy_text(self):
            text = self.text.toPlainText().strip()
            if text:
                self.status.setText("Copied. Paste normally." if clipboard_copy(text)
                                    else "Copy failed. Select and copy the text manually.")

        def closeEvent(self, event):
            self.closing = True
            if self.stream is not None:
                stream, self.stream = self.stream, None
                self.recording = False
                self.work(lambda: close_input_stream(stream), lambda _: None)
            super().closeEvent(event)
            if not self.workers:
                QApplication.instance().quit()

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    def apply_theme(dark):
        background, panel, control = ("#242424", "#1d1d1d", "#343638") if dark else ("#f4f4f4", "#ffffff", "#e5e5e5")
        foreground, muted, border = ("#eeeeee", "#aaaaaa", "#4a4a4a") if dark else ("#202020", "#666666", "#cccccc")
        palette = QPalette()
        for role, color in [
            (QPalette.ColorRole.Window, background),
            (QPalette.ColorRole.WindowText, foreground),
            (QPalette.ColorRole.Base, panel),
            (QPalette.ColorRole.Text, foreground),
            (QPalette.ColorRole.Button, control),
            (QPalette.ColorRole.ButtonText, foreground),
            (QPalette.ColorRole.Highlight, "#1f6aa5"),
            (QPalette.ColorRole.HighlightedText, "#ffffff"),
        ]:
            palette.setColor(role, QColor(color))
        app.setPalette(palette)
        app.setStyleSheet(f"""
            QWidget {{ font-family: 'Sans'; font-size: 13px; color: {foreground}; }}
            QComboBox {{ background: {control}; border: 1px solid {border};
                         border-radius: 7px; padding: 7px 10px; min-height: 18px; }}
            QComboBox::drop-down {{ border: none; width: 22px; }}
            QComboBox QAbstractItemView {{ background: {panel}; selection-background-color: #1f6aa5; }}
            QTextEdit {{ background: {panel}; border: 1px solid {border};
                        border-radius: 8px; padding: 10px; selection-background-color: #1f6aa5; }}
            QPushButton {{ background: {control}; border: none; border-radius: 7px;
                           padding: 10px 18px; min-height: 18px; }}
            QPushButton:hover {{ background: {border}; }}
            QPushButton#recordButton {{ background: #1f6aa5; color: white; font-weight: bold; }}
            QPushButton#recordButton:hover {{ background: #144870; }}
            QPushButton#recordButton[recording="true"] {{ background: #c0392b; }}
            QPushButton:disabled, QComboBox:disabled {{ color: {muted}; }}
            QLabel#statusLabel {{ color: {muted}; }}
            QProgressBar {{ background: {control}; border: none; border-radius: 5px; }}
            QProgressBar::chunk {{ background: #1f6aa5; border-radius: 5px; }}
        """)

    def system_theme():
        scheme = app.styleHints().colorScheme()
        if shutil.which("gsettings"):
            try:
                preference = subprocess.run(
                    ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                    capture_output=True, text=True, timeout=1, check=False,
                ).stdout.strip()
                if preference in ("'prefer-dark'", "'prefer-light'"):
                    return preference == "'prefer-dark'"
            except (OSError, subprocess.TimeoutExpired):
                pass
        return scheme != Qt.ColorScheme.Light

    apply_theme(system_theme())
    app.styleHints().colorSchemeChanged.connect(lambda _: apply_theme(system_theme()))
    # A single GNOME settings subscription also handles platforms where Qt reports Unknown.
    from PySide6.QtCore import QProcess
    theme_monitor = QProcess(app)
    if shutil.which("gsettings"):
        theme_monitor.readyReadStandardOutput.connect(
            lambda: (theme_monitor.readAllStandardOutput(), apply_theme(system_theme()))
        )
        theme_monitor.start("gsettings", ["monitor", "org.gnome.desktop.interface", "color-scheme"])
        app.aboutToQuit.connect(theme_monitor.terminate)
    window = DictationWindow()
    window.show()
    result = app.exec()
    if theme_monitor.state() != QProcess.ProcessState.NotRunning:
        theme_monitor.terminate()
        theme_monitor.waitForFinished(1000)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--quick", action="store_true", help="Record immediately in a compact GUI; Escape closes after pasting")
    parser.add_argument("--terminal", action="store_true", help="Use a terminal instead of the GUI")
    parser.add_argument("--repeat", action="store_true", help="Keep the terminal open for more dictations")
    parser.add_argument("--paste", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=MODELS)
    parser.add_argument("--lang", default="en", help="Language code, or auto (default: en)")
    args = parser.parse_args()
    from pathlib import Path
    import faulthandler
    import signal
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "whisper-dictate"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = logging.FileHandler(state / "runtime.log")
    os.chmod(state / "runtime.log", 0o600)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    # SIGUSR1 can collect Python thread stacks if a future freeze needs diagnosis.
    faulthandler.register(signal.SIGUSR1, file=handler.stream, all_threads=True)
    log.info("Starting %s mode", "terminal" if args.terminal else "GUI")
    if args.terminal and not sys.stdin.isatty():
        parser.error("Run dictation in a terminal so Enter can stop recording.")
    if args.paste:
        print("Auto-paste is disabled. Text will be copied for manual paste.", file=sys.stderr)
    language = None if args.lang == "auto" else args.lang
    try:
        if not args.terminal:
            return run_gui(args.model, language, quick=args.quick)
        print(f"Loading local {args.model} model…", flush=True)
        model = load_whisper_model(args.model)
        while True:
            transcribe_and_copy(model, record_audio(), language)
            if not args.repeat:
                break
            if input("Enter to record again, or q then Enter to quit: ").strip().lower() == "q":
                break
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        log.error("Dictation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    sys.exit(main())
