import json
from pathlib import Path
import shutil
import subprocess
import unittest

from pages import _JS


class PageRuntimeTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for browser polling tests')
    def test_poll_lifecycle(self):
        script = _JS % {'ticket': json.dumps('tk'), 'key': json.dumps('secret'),
                        'base': json.dumps('https://qr.test'), 'ttl': 180}
        result = subprocess.run(
            ['node', str(Path(__file__).with_name('test_poll.js'))],
            input=script, text=True, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
