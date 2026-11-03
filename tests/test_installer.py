import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from install_citybridge import install

TEMPLATE='''"""City Bridge Public Agent Gateway"""\n\ndef setup(): pass\ndef actor(x): pass\ndef clean(x): pass\n\nif __name__=='__main__':\n    pass\n'''

class InstallerTests(unittest.TestCase):
    def test_install_is_idempotent_and_backs_up(self):
        with tempfile.TemporaryDirectory() as temp:
            p=pathlib.Path(temp)/'app.py'
            p.write_text(TEMPLATE,encoding='utf-8')
            m=pathlib.Path(__file__).resolve().parents[1]/'citybridge_growth.py'
            result=install(p,m)
            self.assertIn('.citybridge_backup_',result)
            self.assertTrue(pathlib.Path(result,'app.py').exists())
            self.assertEqual(install(p,m),'ALREADY_INSTALLED')
            self.assertEqual(p.read_text().count('install_growth(app, connect, setup, actor, clean)'),1)

    def test_rejects_wrong_application(self):
        with tempfile.TemporaryDirectory() as temp:
            p=pathlib.Path(temp)/'app.py'
            p.write_text('print(42)',encoding='utf-8')
            m=pathlib.Path(__file__).resolve().parents[1]/'citybridge_growth.py'
            with self.assertRaisesRegex(RuntimeError,'expected existing'):install(p,m)
            self.assertEqual(p.read_text(),'print(42)')

if __name__=='__main__':unittest.main()
