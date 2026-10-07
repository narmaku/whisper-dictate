from pathlib import Path
import subprocess
import sys
import unittest


class ResponsivenessTests(unittest.TestCase):
    def test_slow_microphone_start_stop_keeps_gui_responsive(self):
        self.run_gui_check(False)

    def test_closing_during_microphone_start_closes_stream(self):
        self.run_gui_check(True)

    def test_quick_mode_is_compact_and_records_automatically(self):
        self.run_gui_check(False, quick=True)

    def run_gui_check(self, close_during_start, quick=False):
        code = r'''
import os, time, threading
from types import SimpleNamespace
from unittest.mock import patch, Mock
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import dictate
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
phase = [0]
failures = []
last = [None]
delays = []
threads = []
closed = []
class Stream:
    def start(self):
        threads.append(threading.current_thread().name)
        time.sleep(.45)
    def stop(self):
        raise AssertionError("The draining stop call must not be used")
    def abort(self):
        threads.append(threading.current_thread().name)
        time.sleep(.45)
    def close(self):
        closed.append(True)
def discover():
    threads.append(threading.current_thread().name)
    time.sleep(.45)
    return [{'name': 'Slow test microphone', 'max_input_channels': 1}]
start = time.monotonic()
def check():
    now = time.monotonic()
    if last[0] is not None:
        delays.append(now-last[0])
    last[0] = now
    try:
        window = next(w for w in app.topLevelWidgets() if w.windowTitle().startswith('Whisper Dictate'))
        if QUICK and phase[0] == 0 and window.recording:
            assert not window.mic.isVisible(), 'Quick mode shows full selectors'
            assert not window.text.isVisible(), 'Quick mode shows the full transcript editor'
            assert not window.copy.isVisible(), 'Quick mode shows full action buttons'
            assert window.height() < 200, window.height()
            phase[0] = 1
        elif not QUICK and phase[0] == 0 and window.record.isEnabled():
            phase[0] = 1
            window.toggle_record()
            if CLOSE_DURING_START:
                QTimer.singleShot(50, window.close)
        elif phase[0] == 1 and window.recording and not CLOSE_DURING_START:
            phase[0] = 2
            window.toggle_record()
        elif phase[0] == 2 and not window.audio_busy:
            phase[0] = 3
            window.close()
            return
        if now-start > 5:
            raise AssertionError('Microphone operation did not complete')
        QTimer.singleShot(20, check)
    except Exception as exc:
        failures.append(str(exc))
        app.quit()
QTimer.singleShot(20, check)
with patch.object(dictate.sd, 'query_devices', side_effect=discover), \
     patch.object(dictate.sd, 'default', SimpleNamespace(device=(0, 0))), \
     patch.object(dictate.sd, 'InputStream', side_effect=lambda **kwargs: Stream()), \
     patch.object(dictate, 'WhisperModel', return_value=Mock()):
    result = dictate.run_gui('small', 'en', quick=QUICK)
assert not failures, failures
assert closed, 'The microphone stream leaked'
assert all(name != 'MainThread' for name in threads), threads
assert max(delays) < .25, max(delays)
assert result == 0
print('Responsiveness and cleanup passed')
'''.replace('CLOSE_DURING_START', repr(close_during_start)).replace('QUICK', repr(quick))
        result = subprocess.run([sys.executable, '-c', code],
                                cwd=Path(__file__).parents[1], capture_output=True,
                                text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Responsiveness and cleanup passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
