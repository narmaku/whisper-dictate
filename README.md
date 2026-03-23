# Whisper Dictate

Minimal voice-to-text for Linux using local [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Record speech, transcribe offline, paste into any app. Single Python file, no server, no cloud.

## Setup

```bash
git clone https://github.com/narmaku/whisper-dictate.git
cd whisper-dictate
./setup.sh
```

The setup script creates a Python virtual environment, installs dependencies, and checks for required system tools.

### System dependencies

The app auto-detects your session type (Wayland or X11) and uses the right tools.

| Feature       | Wayland               | X11                  |
|---------------|-----------------------|----------------------|
| Clipboard     | `wl-copy`             | `xclip` or `xsel`   |
| Auto-paste    | `ydotool`             | `xdotool`            |

Install what you need:

```bash
# Fedora/RHEL
sudo dnf install wl-clipboard   # Wayland clipboard
sudo dnf install ydotool         # Wayland auto-paste (for --quick mode)
sudo dnf install xclip xdotool   # X11

# Ubuntu/Debian
sudo apt install wl-clipboard ydotool   # Wayland
sudo apt install xclip xdotool          # X11
```

#### ydotool daemon setup (Wayland only)

ydotool needs its daemon running with accessible socket permissions:

```bash
sudo systemctl enable --now ydotool

# Make the socket accessible to your user (persists across reboots)
sudo mkdir -p /etc/systemd/system/ydotool.service.d
sudo tee /etc/systemd/system/ydotool.service.d/override.conf > /dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/ydotoold --socket-perm 0666
EOF
sudo systemctl daemon-reload
sudo systemctl restart ydotool
```

## Usage

### GUI mode

Full interface with mic, model, and language selectors.

```bash
.venv/bin/python dictate.py
```

1. Select your microphone, model, and language
2. Click **Record** and speak
3. Click **Stop** to transcribe
4. Result appears in the text box — use **Copy** or select and `Ctrl+C`

### Quick mode

Tiny floating popup that records immediately, transcribes on stop, and pastes into the previously active window.

```bash
.venv/bin/python dictate.py --quick
```

1. A small popup appears and recording starts immediately
2. Speak, then press **Escape** or click **Stop**
3. Text is transcribed, copied to clipboard, and auto-pasted

Options:

| Flag      | Description                                           | Default |
|-----------|-------------------------------------------------------|---------|
| `--model` | Whisper model: tiny, base, small, medium, large-v3, turbo | small   |
| `--lang`  | Language code: en, es, fr, de, auto, etc.             | en      |

```bash
# Spanish with medium model
.venv/bin/python dictate.py --quick --model medium --lang es

# Auto-detect language
.venv/bin/python dictate.py --quick --lang auto
```

### Keyboard shortcut

Bind quick mode to a keyboard shortcut in your desktop environment for hands-free dictation anywhere.

**COSMIC** (Settings > Keyboard > Shortcuts > Custom):
```
/path/to/whisper-dictate/.venv/bin/python /path/to/whisper-dictate/dictate.py --quick --lang en
```

**GNOME:**
```bash
# Via gsettings or Settings > Keyboard > Custom Shortcuts
```

**KDE:**
```bash
# Via System Settings > Shortcuts > Custom Shortcuts
```

## Models

Models download automatically on first use and are cached locally.

| Model    | Size   | Speed  | Accuracy   |
|----------|--------|--------|------------|
| tiny     | ~40MB  | Fast   | Basic      |
| base     | ~150MB | Fast   | Good       |
| small    | ~500MB | Medium | Very good  |
| medium   | ~1.5GB | Slow   | Great      |
| large-v3 | ~3GB   | Slower | Best       |
| turbo    | ~1.5GB | Medium | Great      |

`small` is the default — good balance of speed and accuracy for most hardware.

## How it works

1. Audio is captured via [sounddevice](https://python-sounddevice.readthedocs.io/) (PortAudio)
2. Audio is normalized to handle varying mic volumes
3. [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2) transcribes locally on CPU
4. Result is copied to clipboard and optionally auto-pasted via ydotool/xdotool

All processing happens locally. No audio or text leaves your machine.

## License

[MIT](LICENSE)
