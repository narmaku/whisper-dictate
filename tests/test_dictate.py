import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch,Mock

spec=importlib.util.spec_from_file_location('dictate',Path(__file__).parents[1]/'dictate.py')
dictate=importlib.util.module_from_spec(spec)
sys.modules["dictate"] = dictate
spec.loader.exec_module(dictate)

class DictationTests(unittest.TestCase):
    def test_help_runs_without_tkinter(self):
        result=subprocess.run([sys.executable,str(Path(dictate.__file__)),'--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--repeat',result.stdout)

    def test_terminal_does_not_spawn_clipboard_helpers(self):
        with patch('PySide6.QtWidgets.QApplication.instance', return_value=None), \
             patch.object(dictate.subprocess, 'run') as run:
            self.assertFalse(dictate.clipboard_copy('test'))
            run.assert_not_called()

    @patch.dict('os.environ', {'QT_QPA_PLATFORM': 'offscreen'})
    def test_clipboard_preserves_unicode_without_helpers(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        with patch.object(dictate.subprocess, 'run') as run:
            self.assertTrue(dictate.clipboard_copy('こんにちは\nsecond line'))
            self.assertEqual(app.clipboard().text(), 'こんにちは\nsecond line')
            run.assert_not_called()

    @patch('dictate.input',side_effect=KeyboardInterrupt,create=True)
    @patch('dictate.sd.InputStream')
    def test_cancel_closes_microphone(self,stream,read):
        with self.assertRaises(KeyboardInterrupt):dictate.record_audio()
        stream.return_value.abort.assert_called_once()
        stream.return_value.close.assert_called_once()
        stream.return_value.stop.assert_not_called()

    def test_abort_failure_still_closes_microphone(self):
        stream = Mock()
        stream.abort.side_effect = RuntimeError('device disconnected')
        with self.assertRaises(RuntimeError):
            dictate.close_input_stream(stream)
        stream.close.assert_called_once()
        stream.stop.assert_not_called()

    @patch('dictate.clipboard_copy')
    def test_empty_audio_does_not_replace_clipboard(self,copy):
        import numpy as np
        model=Mock()
        self.assertEqual(dictate.transcribe_and_copy(model,np.array([],dtype=np.float32),'en'),'')
        model.transcribe.assert_not_called()
        copy.assert_not_called()

    @patch('dictate.clipboard_copy',return_value=True)
    def test_transcription_is_copied_without_key_injection(self,copy):
        import numpy as np
        model=Mock();model.transcribe.return_value=([Mock(text=' hello'),Mock(text=' world ')],Mock())
        self.assertEqual(dictate.transcribe_and_copy(model,np.ones(16000,dtype=np.float32),'en'),'hello world')
        copy.assert_called_once_with('hello world')

    @patch('dictate.WhisperModel')
    def test_cached_model_does_not_require_network(self, model):
        dictate.load_whisper_model('small')
        model.assert_called_once_with('small', device='cpu', compute_type='int8', local_files_only=True)

    @patch('dictate.WhisperModel', side_effect=[FileNotFoundError('not cached'), Mock()])
    def test_new_model_can_still_download(self, model):
        dictate.load_whisper_model('medium')
        self.assertEqual(model.call_count, 2)
        self.assertNotIn('local_files_only', model.call_args.kwargs)

    @patch.dict('os.environ', {'QT_QPA_PLATFORM': 'offscreen'})
    @patch('dictate.sd.query_devices', return_value=[{'name': 'Test microphone', 'max_input_channels': 1}])
    @patch('dictate.WhisperModel')
    def test_gui_selectors_and_failed_model_recovery(self, model, devices):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from types import SimpleNamespace
        import time
        model.side_effect = lambda name, **kwargs: (_ for _ in ()).throw(RuntimeError('model unavailable')) if name == 'base' else Mock()
        app = QApplication.instance() or QApplication([])
        failures = []
        phase = [0]
        deadline = time.monotonic() + 5
        def check():
            try:
                window = next(w for w in app.topLevelWidgets() if w.windowTitle() == 'Whisper Dictate')
                if phase[0] == 0 and window.model is not None:
                    self.assertEqual(window.models.currentText(), 'small')
                    self.assertEqual(window.languages.currentData(), 'es')
                    self.assertEqual([window.models.itemText(i) for i in range(window.models.count())], dictate.MODELS)
                    self.assertTrue(window.record.isEnabled())
                    with patch.object(dictate.subprocess, 'run') as external_copy:
                        window.transcribed('Hola mi niño, como esta?')
                        self.assertEqual(app.clipboard().text(), 'Hola mi niño, como esta?')
                        window.copy_text()
                        external_copy.assert_not_called()
                    window.languages.setCurrentText('Japanese')
                    self.assertEqual(window.languages.currentData(), 'ja')
                    phase[0] = 1
                    window.models.setCurrentText('base')
                elif phase[0] == 1 and window.status.text().startswith('Error:'):
                    self.assertIn('model unavailable', window.status.text())
                    self.assertTrue(window.models.isEnabled())
                    self.assertFalse(window.record.isEnabled())
                    phase[0] = 2
                    window.close()
                    return
                if time.monotonic() > deadline:
                    raise AssertionError('GUI worker did not complete')
                QTimer.singleShot(20, check)
            except Exception as exc:
                failures.append(exc)
                app.quit()
        QTimer.singleShot(20, check)
        with patch.object(dictate.sd, 'default', SimpleNamespace(device=(0, 0))):
            self.assertEqual(dictate.run_gui('small', 'es'), 0)
        self.assertEqual(failures, [])
        self.assertEqual(phase[0], 2)

if __name__=='__main__':unittest.main()
