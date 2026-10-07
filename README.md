# Whisper Dictate

Local voice-to-text with a graphical microphone, model, and language selector.
Record speech, transcribe it with faster-whisper on your CPU, then paste normally.
No keyboard simulation, privileged input daemon, or Tkinter installation is needed.
The rounded interface follows GNOME’s light/dark appearance setting.

## Setup

```bash
./setup.sh
.venv/bin/python dictate.py
```

The setup installs Python dependencies into `.venv`, including the Qt GUI wheels.
It does not install RPM packages or change system services. Audio uses PortAudio
through sounddevice; this Fedora machine already has PortAudio installed.

## GUI

1. Choose your microphone, model, and language (or Auto).
2. Click Record, speak, then click Stop.
3. The transcript appears and is copied to the clipboard. Copy copies the whole
   text box if you accumulate multiple dictations; Clear empties the text box.
4. Paste in the destination app with its normal shortcut: Ctrl+V in most apps,
   or Ctrl+Shift+V in terminals.

Models download on first use and stay cached. The `small` model is the default.
The language/model selectors remain available between recordings.

```bash
# Full GUI with Spanish and a different model
.venv/bin/python dictate.py --model medium --lang es

# Quick GUI: start recording when ready; Escape closes after you paste
.venv/bin/python dictate.py --quick --model small --lang en
```

Quick mode never pastes automatically. The window stays open after copying;
paste in your destination app, then press Escape to close it. The former `--paste` option is
accepted for old shortcuts but only prints a notice; it does not inject keys.
Quick mode uses a compact floating window with a Stop button and recording meter;
the transcript appears after transcription. Model, language, and microphone
selectors are available in the full GUI.

On this GNOME desktop, **Alt+Space** starts quick mode and **Ctrl+Alt+Space** opens
the full GUI. Escape in the quick window cancels recording and closes it.
The GUI uses Qt’s built-in clipboard. It never launches `wl-copy`, xclip, or xsel,
so there are no clipboard helper windows or their focus notifications. Keep the
window open until you have pasted; clipboard persistence after closing depends
on your desktop clipboard manager.

## Optional terminal mode

```bash
.venv/bin/python dictate.py --terminal
.venv/bin/python dictate.py --terminal --repeat --lang auto
```

Speak, then press Enter to stop recording. Ctrl+C cancels. The transcript is
printed for manual selection and copying, with no clipboard helper or simulated paste.

## Checks

```bash
.venv/bin/python -m unittest discover -s tests
```

All audio processing and transcription happen locally. Model downloads contact
Hugging Face; microphone audio is not uploaded.

Microphone discovery, start/stop, model loading, and transcription run in background
workers so the window stays responsive. Input is stopped using PortAudio abort,
avoiding a draining-stop hang observed with this machine’s audio backend.
Downloaded models load offline first; only uncached models need network access.
Diagnostic milestones (without transcript text) are saved to
`~/.local/state/whisper-dictate/runtime.log`.
