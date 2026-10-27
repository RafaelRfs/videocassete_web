"""Pastas por sistema, busca de ferramentas, ID da extensão e isolamento de cookies por servidor."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from util import ROOT, TMP_HOME

import platform_paths as plat
import stream_worker as worker


class PathsTest(unittest.TestCase):
    def test_override(self):
        self.assertEqual(plat.DATA_DIR, TMP_HOME)
        self.assertEqual(worker.JOBS_DIR, TMP_HOME / 'jobs')

    def test_defaults_per_os(self):
        env = {k: v for k, v in os.environ.items() if k != 'VIDEOCASSETE_HOME'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(plat, 'IS_WIN', False), mock.patch.object(plat, 'IS_MAC', True):
                self.assertEqual(plat.data_dir(), Path.home() / 'Library' / 'Application Support' / 'VideocasseteWeb')
            with mock.patch.object(plat, 'IS_WIN', False), mock.patch.object(plat, 'IS_MAC', False):
                os.environ.pop('XDG_DATA_HOME', None)
                self.assertEqual(plat.data_dir(), Path.home() / '.local' / 'share' / 'videocassete_web')
            with mock.patch.object(plat, 'IS_WIN', True), mock.patch.object(plat, 'IS_MAC', False):
                os.environ['LOCALAPPDATA'] = str(TMP_HOME / 'local')
                self.assertEqual(plat.data_dir(), TMP_HOME / 'local' / 'VideocasseteWeb')

    def test_tools_file_wins(self):
        fake = TMP_HOME / ('fake-ffmpeg' + ('.exe' if os.name == 'nt' else ''))
        fake.write_text('x')
        plat.TOOLS_FILE.write_text(json.dumps({'ffmpeg': str(fake)}))
        try:
            self.assertEqual(plat.find_tool('ffmpeg'), str(fake))
        finally:
            plat.TOOLS_FILE.unlink()
            fake.unlink()

    def test_install_hints(self):
        for tool in ('ffmpeg', 'yt-dlp', 'deno'):
            self.assertTrue(plat.install_hint(tool))

    def test_tls_context(self):
        ctx = plat.SSL_CONTEXT
        if ctx is not None:
            self.assertGreater(ctx.cert_store_stats()['x509_ca'], 0)


class ExtensionIdTest(unittest.TestCase):
    def test_id_matches_key(self):
        sys.path.insert(0, str(ROOT))
        import install
        ext_id = install.extension_id()
        self.assertRegex(ext_id, r'^[a-p]{32}$')
        chromium, firefox = install.manifests(Path('/x/launcher'))
        self.assertEqual(chromium['allowed_origins'], [f'chrome-extension://{ext_id}/'])
        manifest = json.loads((ROOT / 'extension' / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(firefox['allowed_extensions'], [manifest['browser_specific_settings']['gecko']['id']])
        self.assertEqual(chromium['name'], plat.HOST_NAME)


class CookieIsolationTest(unittest.TestCase):
    def test_cookie_and_token_stay_with_owner(self):
        headers = {'Cookie': 'manifest=1', 'Authorization': 'Bearer abc', 'Referer': 'https://site/',
                   '__origin_host__': 'cdn-a.example',
                   '__host_cookies__': {'cdn-b.example': 'b=2'}}
        a = worker.request_headers('https://cdn-a.example/master.m3u8', headers)
        b = worker.request_headers('https://cdn-b.example/seg1.ts', headers)
        c = worker.request_headers('https://cdn-c.example/seg1.ts', headers)
        self.assertEqual(a['Cookie'], 'manifest=1')
        self.assertEqual(a['Authorization'], 'Bearer abc')
        self.assertEqual(b['Cookie'], 'b=2')
        self.assertNotIn('Authorization', b)
        self.assertNotIn('Cookie', c)
        self.assertEqual(c['Referer'], 'https://site/')


if __name__ == '__main__':
    unittest.main()
