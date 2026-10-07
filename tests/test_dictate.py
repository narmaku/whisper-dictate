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

    @patch.dict('os.environ',{'XDG_SESSION_TYPE':'wayland'})
    @patch('dictate.shutil.which',return_value='/usr/bin/wl-copy')
    @patch('dictate.subprocess.run',return_value=Mock(returncode=1,stderr=b'clipboard unavailable'))
    def test_clipboard_failure_is_reported(self,run,which):
        self.assertFalse(dictate.clipboard_copy('test'))

    @patch.dict('os.environ',{'XDG_SESSION_TYPE':'wayland'})
    @patch('dictate.shutil.which',return_value='/usr/bin/wl-copy')
    @patch('dictate.subprocess.run',return_value=Mock(returncode=0))
    def test_clipboard_preserves_unicode_via_stdin(self,run,which):
        self.assertTrue(dictate.clipboard_copy('こんにちは\nsecond line'))
        self.assertEqual(run.call_args.kwargs['input'],'こんにちは\nsecond line'.encode())
        self.assertEqual(run.call_args.args[0],['wl-copy'])

    @patch('dictate.input',side_effect=KeyboardInterrupt,create=True)
    @patch('dictate.sd.InputStream')
    def test_cancel_closes_microphone(self,stream,read):
        with self.assertRaises(KeyboardInterrupt):dictate.record_audio()
        self.assertTrue(stream.return_value.__exit__.called)

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
