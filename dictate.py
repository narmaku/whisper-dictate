#!/usr/bin/env python3
"""Whisper Dictate - Minimal push-to-talk voice-to-text using local Whisper.

Usage:
  dictate.py              Full GUI mode
  dictate.py --quick      Quick mode: record → transcribe → paste into active window
  dictate.py --quick --model medium --lang es
"""

import argparse
import logging
import os
import shutil
import subprocess
import threading
import time
import tkinter as tk

import customtkinter as ctk
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

__version__ = "0.1.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("dictate")

SAMPLE_RATE = 16000
MODELS = ["tiny", "base", "small", "medium", "large-v3", "turbo"]
DEFAULT_MODEL = "small"
LANGUAGES = {
    "Auto": None,
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Italian": "it",
    "Portuguese": "pt",
    "Russian": "ru",
    "Chinese": "zh",
    "Japanese": "ja",
    "Korean": "ko",
    "Arabic": "ar",
    "Hindi": "hi",
    "Dutch": "nl",
    "Polish": "pl",
    "Turkish": "tr",
    "Ukrainian": "uk",
    "Czech": "cs",
    "Swedish": "sv",
}
LANG_CODES = {v: k for k, v in LANGUAGES.items() if v}  # "en" -> "English"
DEFAULT_LANGUAGE = "English"


def get_input_devices():
    devices = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            devices.append((i, d["name"]))
    return devices


def is_wayland():
    return os.environ.get("XDG_SESSION_TYPE") == "wayland"


def clipboard_copy(text):
    """Copy text to clipboard using the appropriate tool for the session type."""
    if is_wayland() and shutil.which("wl-copy"):
        subprocess.run(["wl-copy", text], check=False)
    elif shutil.which("xclip"):
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text.encode(), check=False,
        )
    elif shutil.which("xsel"):
        subprocess.run(
            ["xsel", "--clipboard", "--input"],
            input=text.encode(), check=False,
        )
    else:
        log.warning("No clipboard tool found (install wl-copy, xclip, or xsel)")
        return False
    return True


def simulate_paste():
    """Simulate Ctrl+V using the appropriate tool for the session type."""
    if is_wayland() and shutil.which("wtype"):
        result = subprocess.run(
            ["wtype", "-M", "ctrl", "-k", "v"],
            check=False, capture_output=True,
        )
        if result.returncode == 0:
            log.info("Pasted via Ctrl+V (wtype)")
            return True
        log.warning("wtype failed: %s", result.stderr.decode().strip())

    if shutil.which("ydotool"):
        env = {**os.environ, "YDOTOOL_SOCKET": "/tmp/.ydotool_socket"}
        result = subprocess.run(
            ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
            check=False, env=env, capture_output=True,
        )
        if result.returncode == 0:
            log.info("Pasted via Ctrl+V (ydotool)")
            return True
        log.warning("ydotool failed: %s", result.stderr.decode().strip())

    if not is_wayland() and shutil.which("xdotool"):
        subprocess.run(["xdotool", "key", "ctrl+v"], check=False)
        log.info("Pasted via Ctrl+V (xdotool)")
        return True

    log.warning("Auto-paste unavailable. Text is in clipboard — paste manually with Ctrl+V.")
    return False


# ---------------------------------------------------------------------------
# Quick mode: tiny popup, record, transcribe, paste into previous window
# ---------------------------------------------------------------------------

class QuickDictate:
    def __init__(self, model_name, lang, auto_paste=False):
        self.model_name = model_name
        self.lang = lang
        self.auto_paste = auto_paste
        self.model = None
        self.recording = False
        self.audio_chunks = []
        self.peak_level = 0.0
        self.result_text = None

    def run(self):
        log.info("Quick mode: model=%s, lang=%s, paste=%s",
                 self.model_name, self.lang or "auto", self.auto_paste)
        ctk.set_appearance_mode("dark")

        self.root = ctk.CTk()
        self.root.title("Dictating...")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)

        # -- Build compact popup --
        frame = ctk.CTkFrame(self.root)
        frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self.status_label = ctk.CTkLabel(
            frame, text="Loading model...", font=("Sans", 13),
        )
        self.status_label.pack(padx=16, pady=(12, 8))

        bottom = ctk.CTkFrame(frame, fg_color="transparent")
        bottom.pack(fill=tk.X, padx=12, pady=(0, 12))
        bottom.columnconfigure(0, weight=1)

        self.level_bar = ctk.CTkProgressBar(bottom, height=12)
        self.level_bar.grid(row=0, column=0, sticky=tk.EW, padx=(0, 10))
        self.level_bar.set(0)

        self.stop_btn = ctk.CTkButton(
            bottom, text="Stop (Esc)", width=90, height=30,
            command=self._stop, fg_color="#c0392b", hover_color="#e74c3c",
        )
        self.stop_btn.grid(row=0, column=1)

        # Size and center on screen
        self.root.update_idletasks()
        w, h = 340, 110
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        self.root.bind("<Escape>", lambda _: self._stop())
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)

        # Start recording immediately, load model in parallel
        self._start_recording()
        threading.Thread(target=self._load_model, daemon=True).start()

        self.root.mainloop()

        if self.result_text and self.auto_paste:
            time.sleep(1.0)
            simulate_paste()

    def _load_model(self):
        log.info("Loading model: %s", self.model_name)
        self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        log.info("Model loaded: %s", self.model_name)
        try:
            self.root.after(0, lambda: self.status_label.configure(
                text="Recording... (Esc to stop)",
            ))
        except Exception:
            pass

    def _start_recording(self):
        self.recording = True
        self.audio_chunks.clear()

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            callback=self._audio_callback, blocksize=1024,
        )
        self.stream.start()
        self._update_level()
        log.info("Recording started")

    def _stop(self):
        if not self.recording:
            return
        self.recording = False
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass

        self.level_bar.set(0)
        self.stop_btn.configure(state="disabled")
        self.status_label.configure(text="Transcribing...")
        log.info("Recording stopped, %.1fs of audio",
                 len(self.audio_chunks) * 1024 / SAMPLE_RATE)

        # Non-daemon so it finishes before process exits
        threading.Thread(target=self._transcribe_and_finish).start()

    def _cancel(self):
        self.recording = False
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        log.info("Cancelled")
        self.root.destroy()

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            log.warning("Audio status: %s", status)
        self.peak_level = float(np.max(np.abs(indata)))
        self.audio_chunks.append(indata.copy())

    def _update_level(self):
        if not self.recording:
            return
        self.level_bar.set(min(self.peak_level * 5, 1.0))
        self.root.after(50, self._update_level)

    def _transcribe_and_finish(self):
        # Wait for model if still loading
        while self.model is None:
            time.sleep(0.1)

        if not self.audio_chunks:
            log.warning("No audio captured")
            try:
                self.root.after(0, self.root.destroy)
            except Exception:
                pass
            return

        audio = np.concatenate(self.audio_chunks).flatten()
        self.audio_chunks.clear()

        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio / peak * 0.95

        log.info("Transcribing %.1fs of audio...", len(audio) / SAMPLE_RATE)
        segments, info = self.model.transcribe(audio, beam_size=5, language=self.lang)
        text = " ".join(seg.text for seg in segments).strip()

        log.info("Result (detected=%s, prob=%.2f): %s",
                 info.language, info.language_probability,
                 text[:100] if text else "(empty)")

        if text:
            clipboard_copy(text)
            log.info("Copied %d chars to clipboard", len(text))
            self.result_text = text

        def _finish():
            if text and not self.auto_paste:
                preview = text[:60] + ("..." if len(text) > 60 else "")
                self.status_label.configure(text=f"Copied: {preview}")
                self.root.after(1500, self.root.destroy)
            else:
                self.root.destroy()

        try:
            self.root.after(0, _finish)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Full GUI mode
# ---------------------------------------------------------------------------

class WhisperDictate:
    def __init__(self, root):
        self.root = root
        self.root.title("Whisper Dictate")
        self.root.geometry("620x440")
        self.root.minsize(450, 320)

        self.recording = False
        self.audio_chunks = []
        self.model = None
        self.peak_level = 0.0

        self._build_gui()
        self._load_model()

    def _build_gui(self):
        # -- Config row --
        config = ctk.CTkFrame(self.root)
        config.pack(fill=tk.X, padx=10, pady=(10, 5))
        config.columnconfigure(1, weight=1)

        ctk.CTkLabel(config, text="Mic:").grid(row=0, column=0, padx=(10, 4), pady=8)
        self.input_devices = get_input_devices()
        device_names = [name for _, name in self.input_devices]
        self.mic_var = ctk.StringVar()
        self.mic_combo = ctk.CTkComboBox(
            config, variable=self.mic_var, values=device_names, state="readonly",
        )
        self.mic_combo.grid(row=0, column=1, sticky=tk.EW, padx=4, pady=8)

        default_idx = sd.default.device[0]
        for i, (dev_idx, name) in enumerate(self.input_devices):
            if dev_idx == default_idx:
                self.mic_combo.set(name)
                break
        else:
            if device_names:
                self.mic_combo.set(device_names[0])

        ctk.CTkLabel(config, text="Model:").grid(row=0, column=2, padx=(8, 4), pady=8)
        self.model_var = ctk.StringVar(value=DEFAULT_MODEL)
        self.model_combo = ctk.CTkComboBox(
            config, variable=self.model_var, values=MODELS,
            state="readonly", width=100, command=lambda _: self._load_model(),
        )
        self.model_combo.grid(row=0, column=3, padx=4, pady=8)

        ctk.CTkLabel(config, text="Lang:").grid(row=0, column=4, padx=(8, 4), pady=8)
        self.lang_var = ctk.StringVar(value=DEFAULT_LANGUAGE)
        self.lang_combo = ctk.CTkComboBox(
            config, variable=self.lang_var, values=list(LANGUAGES.keys()),
            state="readonly", width=110,
        )
        self.lang_combo.grid(row=0, column=5, padx=(4, 10), pady=8)

        # -- Text area --
        self.textbox = ctk.CTkTextbox(self.root, wrap=tk.WORD, font=("Sans", 13))
        self.textbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # -- Buttons --
        btn_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        btn_frame.columnconfigure((0, 1, 2), weight=1, uniform="btn")

        self.record_btn = ctk.CTkButton(
            btn_frame, text="Record", command=self.toggle_record,
            font=("Sans", 13, "bold"), height=36,
        )
        self.record_btn.grid(row=0, column=0, sticky=tk.EW, padx=(0, 4))

        ctk.CTkButton(
            btn_frame, text="Copy", command=self.copy_text,
            font=("Sans", 13), height=36, fg_color="#555", hover_color="#666",
        ).grid(row=0, column=1, sticky=tk.EW, padx=4)

        ctk.CTkButton(
            btn_frame, text="Clear", command=self.clear_text,
            font=("Sans", 13), height=36, fg_color="#555", hover_color="#666",
        ).grid(row=0, column=2, sticky=tk.EW, padx=(4, 0))

        # -- Status bar --
        status_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        status_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        status_frame.columnconfigure(0, weight=1)

        self.status = ctk.CTkLabel(
            status_frame, text="Loading model...", anchor=tk.W, font=("Sans", 11),
        )
        self.status.grid(row=0, column=0, sticky=tk.EW)

        self.level_bar = ctk.CTkProgressBar(status_frame, width=120, height=12)
        self.level_bar.grid(row=0, column=1, sticky=tk.E, padx=(8, 0))
        self.level_bar.set(0)

    def _load_model(self):
        model_name = self.model_var.get()
        log.info("Loading model: %s", model_name)
        self.record_btn.configure(state="disabled")
        self.status.configure(text=f"Loading {model_name} model...")

        def load():
            self.model = WhisperModel(model_name, device="cpu", compute_type="int8")
            log.info("Model loaded: %s", model_name)
            self.root.after(0, self._on_model_loaded, model_name)

        threading.Thread(target=load, daemon=True).start()

    def _on_model_loaded(self, name):
        self.status.configure(text=f"Ready ({name})")
        self.record_btn.configure(state="normal")

    def _selected_device_index(self):
        name = self.mic_var.get()
        for dev_idx, dev_name in self.input_devices:
            if dev_name == name:
                return dev_idx
        return None

    def _selected_language(self):
        return LANGUAGES.get(self.lang_var.get())

    # -- Recording --

    def toggle_record(self):
        if not self.recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        self.recording = True
        self.audio_chunks.clear()
        self.record_btn.configure(text="Stop", fg_color="#c0392b", hover_color="#e74c3c")
        self.mic_combo.configure(state="disabled")
        self.model_combo.configure(state="disabled")
        self.status.configure(text="Recording...")
        log.info("Recording started (device=%s, lang=%s)",
                 self.mic_var.get(), self.lang_var.get())

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            device=self._selected_device_index(),
            callback=self._audio_callback, blocksize=1024,
        )
        self.stream.start()
        self._update_level()

    def _stop_recording(self):
        self.recording = False
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass

        self.level_bar.set(0)
        self.record_btn.configure(text="Record", fg_color="#1f6aa5", hover_color="#144870")
        self.mic_combo.configure(state="readonly")
        self.model_combo.configure(state="readonly")

        duration = len(self.audio_chunks) * 1024 / SAMPLE_RATE
        log.info("Recording stopped (%.1fs of audio)", duration)

        if not self.audio_chunks:
            log.warning("No audio captured")
            self.status.configure(text=f"Ready ({self.model_var.get()})")
            return

        self.status.configure(text="Transcribing...")
        self.record_btn.configure(state="disabled")
        threading.Thread(target=self._transcribe, daemon=True).start()

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            log.warning("Audio callback status: %s", status)
        self.peak_level = float(np.max(np.abs(indata)))
        self.audio_chunks.append(indata.copy())

    def _update_level(self):
        if not self.recording:
            return
        self.level_bar.set(min(self.peak_level * 5, 1.0))
        self.root.after(50, self._update_level)

    # -- Transcription --

    def _transcribe(self):
        audio = np.concatenate(self.audio_chunks).flatten()
        self.audio_chunks.clear()

        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio / peak * 0.95

        lang = self._selected_language()
        log.info("Transcribing %.1fs of audio (lang=%s)...",
                 len(audio) / SAMPLE_RATE, lang or "auto")

        segments, info = self.model.transcribe(audio, beam_size=5, language=lang)
        text = " ".join(seg.text for seg in segments).strip()

        log.info("Result (detected=%s, prob=%.2f): %s",
                 info.language, info.language_probability,
                 text[:100] if text else "(empty)")

        if text:
            self.root.after(0, self._append_text, text)

        model_name = self.model_var.get()
        self.root.after(0, lambda: self.status.configure(text=f"Ready ({model_name})"))
        self.root.after(0, lambda: self.record_btn.configure(state="normal"))

    def _append_text(self, text):
        current = self.textbox.get("1.0", tk.END).strip()
        if current:
            self.textbox.insert(tk.END, " " + text)
        else:
            self.textbox.insert(tk.END, text)
        self.textbox.see(tk.END)

    def copy_text(self):
        text = self.textbox.get("1.0", tk.END).strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status.configure(text="Copied to clipboard!")
            log.info("Copied %d chars to clipboard", len(text))

    def clear_text(self):
        self.textbox.delete("1.0", tk.END)
        log.info("Text cleared")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Whisper Dictate")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Quick mode: record, transcribe, copy to clipboard",
    )
    parser.add_argument(
        "--paste", action="store_true",
        help="Auto-paste into the active window after transcription (quick mode only)",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, choices=MODELS,
        help=f"Whisper model size (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--lang", default="en",
        help="Language code: en, es, fr, auto, etc. (default: en)",
    )
    args = parser.parse_args()

    lang = None if args.lang == "auto" else args.lang

    if args.quick:
        QuickDictate(model_name=args.model, lang=lang, auto_paste=args.paste).run()
    else:
        log.info("Starting Whisper Dictate (GUI mode)")
        ctk.set_appearance_mode("dark")
        root = ctk.CTk()
        WhisperDictate(root)
        root.mainloop()


if __name__ == "__main__":
    main()
