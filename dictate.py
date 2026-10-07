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


def clipboard_copy(text):
    """Copy through the session clipboard; never inject keyboard events."""
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        candidates = [("wl-copy", [])]
    else:
        candidates = [("xclip", ["-selection", "clipboard"]),
                      ("xsel", ["--clipboard", "--input"])]
    for command, args in candidates:
        if not shutil.which(command):
            continue
        try:
            result = subprocess.run([command, *args], input=text.encode(),
                                    capture_output=True, check=False)
        except OSError as exc:
            log.error("Clipboard copy failed: %s", exc)
            return False
        if result.returncode:
            log.error("Clipboard copy failed: %s",
                      result.stderr.decode(errors="replace").strip())
            return False
        return True
    log.error("No clipboard command available; the transcript is printed below.")
    return False


def record_audio():
    """Record until Enter; closing the stream also handles Ctrl+C cancellation."""
    chunks = []

    def capture(indata, frames, time_info, status):
        if status:
            log.warning("Audio: %s", status)
        chunks.append(indata.copy())

    print("Recording… Speak, then press Enter to stop. Ctrl+C cancels.", flush=True)
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        callback=capture, blocksize=1024):
        input()
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
        print("Copy failed. Select the transcript above to copy it manually.", flush=True)
    return text


def run_gui(model_name, language, quick=False):
    # Imported only for GUI use, so CLI diagnostics do not require a display.
    from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot, Qt
    from PySide6.QtGui import QKeySequence, QShortcut, QPalette, QColor
    from PySide6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QComboBox, QPushButton, QTextEdit,
    )

    class Signals(QObject):
        result = Signal(object)
        error = Signal(str)
        finished = Signal()

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
                self.signals.finished.emit()

    class DictationWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Whisper Dictate")
            self.resize(640, 380)
            if quick:
                self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
                self.resize(640, 220)
            self.model = None
            self.stream = None
            self.chunks = []
            self.lock = threading.Lock()
            self.workers = []
            self.quick_pending = quick
            layout = QVBoxLayout(self)
            selectors = QHBoxLayout()
            self.mic = QComboBox()
            for index, device in enumerate(sd.query_devices()):
                if device["max_input_channels"] > 0:
                    self.mic.addItem(device["name"], index)
            default = self.mic.findData(sd.default.device[0])
            if default >= 0:
                self.mic.setCurrentIndex(default)
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
                selectors.addWidget(QLabel(name))
                selectors.addWidget(widget)
            layout.addLayout(selectors)
            self.text = QTextEdit()
            self.text.setPlaceholderText("Your transcription will appear here.")
            layout.addWidget(self.text)
            buttons = QHBoxLayout()
            self.record = QPushButton("Record")
            self.copy = QPushButton("Copy")
            self.clear = QPushButton("Clear")
            for button in [self.record, self.copy, self.clear]:
                buttons.addWidget(button)
            layout.addLayout(buttons)
            self.status = QLabel("Loading model…")
            layout.addWidget(self.status)
            if quick:
                self.cancel_shortcut = QShortcut(QKeySequence("Escape"), self)
                self.cancel_shortcut.activated.connect(self.close)
            self.record.clicked.connect(self.toggle_record)
            self.copy.clicked.connect(self.copy_text)
            self.clear.clicked.connect(self.text.clear)
            self.models.currentTextChanged.connect(self.load_model)
            self.load_model()

        def work(self, function, result):
            worker = Worker(function)
            self.workers.append(worker)
            worker.signals.result.connect(result)
            worker.signals.error.connect(self.failed)
            worker.signals.finished.connect(lambda: self.workers.remove(worker))
            QThreadPool.globalInstance().start(worker)

        def controls(self, busy):
            for widget in [self.mic, self.models, self.languages]:
                widget.setEnabled(not busy)
            self.record.setEnabled(not busy and self.model is not None)

        def load_model(self, *_):
            self.model = None
            self.controls(True)
            name = self.models.currentText()
            self.status.setText(f"Loading {name} model…")
            self.work(lambda: WhisperModel(name, device="cpu", compute_type="int8"),
                      self.model_loaded)

        @Slot(object)
        def model_loaded(self, model):
            self.model = model
            self.controls(False)
            self.status.setText("Ready. Record speech, then copy and paste normally.")
            if self.quick_pending:
                self.quick_pending = False
                self.toggle_record()

        @Slot(str)
        def failed(self, message):
            self.quick_pending = False
            self.controls(False)
            self.record.setText("Record")
            self.status.setText(f"Error: {message}")

        def capture(self, indata, frames, time_info, status):
            if status:
                log.warning("Audio: %s", status)
            with self.lock:
                self.chunks.append(indata.copy())

        def toggle_record(self):
            if self.stream is None:
                self.chunks = []
                try:
                    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                            dtype="float32", device=self.mic.currentData(),
                                            callback=self.capture, blocksize=1024)
                    try:
                        stream.start()
                    except Exception:
                        stream.close()
                        raise
                    self.stream = stream
                except Exception as exc:
                    self.failed(str(exc))
                    return
                self.controls(True)
                self.record.setEnabled(True)
                self.record.setText("Stop")
                self.status.setText("Recording… Click Stop when finished.")
                return
            stream, self.stream = self.stream, None
            try:
                stream.stop()
            finally:
                stream.close()
            with self.lock:
                audio = np.concatenate(self.chunks).flatten() if self.chunks else np.array([])
                self.chunks = []
            self.record.setText("Record")
            self.record.setEnabled(False)
            self.status.setText("Transcribing…")
            lang = self.languages.currentData()
            self.work(lambda: transcribe_and_copy(self.model, audio, lang, copy_result=False),
                      self.transcribed)

        @Slot(object)
        def transcribed(self, text):
            self.controls(False)
            if not text:
                self.status.setText("No speech detected. Clipboard unchanged.")
                return
            previous = self.text.toPlainText().strip()
            self.text.setPlainText((previous + " " + text).strip())
            copied = clipboard_copy(text)
            self.status.setText("Copied. Paste normally." if copied else
                                "Copy failed. Select and copy the transcript manually.")
            if quick and copied:
                self.status.setText("Text copied. Paste normally in your app.")
                QTimer.singleShot(1500, self.close)

        def copy_text(self):
            text = self.text.toPlainText().strip()
            if text:
                self.status.setText("Copied. Paste normally." if clipboard_copy(text)
                                    else "Copy failed. Select and copy the text manually.")

        def closeEvent(self, event):
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
                self.stream = None
            super().closeEvent(event)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, "#252525"),
        (QPalette.ColorRole.WindowText, "#eeeeee"),
        (QPalette.ColorRole.Base, "#181818"),
        (QPalette.ColorRole.Text, "#eeeeee"),
        (QPalette.ColorRole.Button, "#353535"),
        (QPalette.ColorRole.ButtonText, "#eeeeee"),
        (QPalette.ColorRole.Highlight, "#1f6aa5"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
    ]:
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    window = DictationWindow()
    window.show()
    return app.exec()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--quick", action="store_true", help="Record immediately in the GUI and close after transcription")
    parser.add_argument("--terminal", action="store_true", help="Use a terminal instead of the GUI")
    parser.add_argument("--repeat", action="store_true", help="Keep the terminal open for more dictations")
    parser.add_argument("--paste", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=MODELS)
    parser.add_argument("--lang", default="en", help="Language code, or auto (default: en)")
    args = parser.parse_args()
    if args.terminal and not sys.stdin.isatty():
        parser.error("Run dictation in a terminal so Enter can stop recording.")
    if args.paste:
        print("Auto-paste is disabled. Text will be copied for manual paste.", file=sys.stderr)
    language = None if args.lang == "auto" else args.lang
    try:
        if not args.terminal:
            return run_gui(args.model, language, quick=args.quick)
        print(f"Loading local {args.model} model…", flush=True)
        model = WhisperModel(args.model, device="cpu", compute_type="int8")
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
