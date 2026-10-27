"""Downloads de verdade contra um servidor local: HLS, HLS AES-128, DASH, retomada e o fluxo pelo host."""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from util import FFMPEG, TMP_HOME, Server, make_dash, make_hls, make_source, native_call, probe_duration

import stream_worker as worker


@unittest.skipUnless(FFMPEG, 'ffmpeg não encontrado')
class DownloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = Path(tempfile.mkdtemp(prefix='vcw-media-'))
        cls.src = make_source(cls.media)
        cls.out = Path(tempfile.mkdtemp(prefix='vcw-out-'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.media, ignore_errors=True)
        shutil.rmtree(cls.out, ignore_errors=True)

    def serve(self, kind, **kw):
        folder = Path(tempfile.mkdtemp(dir=self.media))
        name = {'hls': make_hls, 'dash': make_dash}[kind](folder, self.src, **kw)
        server = Server(folder)
        self.addCleanup(server.close)
        return server, server.url(name)

    def run_job(self, url, title, threads=4):
        d = worker.new_job_dir(url, title, {}, 'best', threads, str(self.out), lang='en')
        return d, worker.run_job(d)

    def assert_video(self, path, seconds=6):
        self.assertTrue(Path(path).is_file(), path)
        self.assertEqual(Path(path).suffix, '.mp4')
        duration = probe_duration(path)
        if duration is not None:
            self.assertAlmostEqual(duration, seconds, delta=1.0)

    def test_hls(self):
        _, url = self.serve('hls')
        info = worker.probe(url, {})
        self.assertEqual(info['kind'], 'hls')
        self.assertAlmostEqual(info['duration'], 6, delta=1)
        _, st = self.run_job(url, 'hls test')
        self.assertEqual(st['status'], 'done', st.get('error'))
        self.assert_video(st['output'])

    def test_hls_aes128(self):
        _, url = self.serve('hls', aes=True)
        _, st = self.run_job(url, 'hls aes')
        self.assertEqual(st['status'], 'done', st.get('error'))
        self.assert_video(st['output'])

    def test_dash(self):
        _, url = self.serve('dash')
        info = worker.probe(url, {})
        self.assertEqual(info['kind'], 'dash')
        _, st = self.run_job(url, 'dash test')
        self.assertEqual(st['status'], 'done', st.get('error'))
        self.assert_video(st['output'])

    def test_resume_downloads_only_missing(self):
        server, url = self.serve('hls')
        server.fail = {'seg003.ts'}
        d, st = self.run_job(url, 'resume test', threads=1)
        self.assertEqual(st['status'], 'error')
        self.assertIn('HTTP 404', st['error'])
        before = {name for name, _ in server.requests if name.endswith('.ts') and name != 'seg003.ts'}
        server.fail = set()
        server.requests.clear()
        st = worker.run_job(d)
        self.assertEqual(st['status'], 'done', st.get('error'))
        again = {name for name, _ in server.requests if name.endswith('.ts')}
        self.assertIn('seg003.ts', again)
        self.assertFalse(before & again, 'retomada baixou de novo segmentos que já tinha')
        self.assert_video(st['output'])

    def test_live_refused(self):
        folder = Path(tempfile.mkdtemp(dir=self.media))
        (folder / 'live.m3u8').write_text('#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\na.ts\n', encoding='utf-8')
        server = Server(folder)
        self.addCleanup(server.close)
        d, st = self.run_job(server.url('live.m3u8'), 'live')
        self.assertEqual(st['status'], 'error')
        self.assertIn('live', st['error'])

    def test_through_native_host(self):
        """Fluxo da extensão: download -> worker separado do host -> list até terminar."""
        _, url = self.serve('hls')
        reply = native_call([{'cmd': 'download', 'url': url, 'title': 'via host', 'quality': 'best',
                              'threads': 4, 'out_dir': str(self.out), 'lang': 'pt-BR'}])[0]
        self.assertTrue(reply['ok'], reply)
        job_id = reply['id']
        self.assertEqual(json.loads((TMP_HOME / 'jobs' / job_id / 'job.json').read_text(encoding='utf-8'))['lang'], 'pt')
        deadline = time.time() + 90
        job = None
        while time.time() < deadline:
            jobs = native_call([{'cmd': 'list'}])[0]['jobs']
            job = next(j for j in jobs if j['id'] == job_id)
            if job['status'] in ('done', 'error', 'interrupted'):
                break
            time.sleep(0.5)
        self.assertEqual(job['status'], 'done', job)
        self.assert_video(job['output'])
        self.assertTrue(native_call([{'cmd': 'remove', 'id': job_id}])[0]['ok'])


class HostProtocolTest(unittest.TestCase):
    def test_ping_and_errors(self):
        ping, unknown, missing = native_call([{'cmd': 'ping', 'reqId': 1}, {'cmd': 'nope', 'lang': 'pt'},
                                              {'cmd': 'cancel', 'id': '../etc', 'lang': 'en'}])
        self.assertTrue(ping['ok'])
        self.assertEqual(ping['reqId'], 1)
        self.assertEqual(ping['version'], '2.0.0')
        self.assertIn('hints', ping)
        self.assertIn('desconhecido', unknown['error'])
        self.assertFalse(missing['ok'])
        self.assertIn('not found', missing['error'])

    def test_recording_flow(self):
        """rec_start + pedaços + rec_stop, como a gravação pela página (sem ffmpeg: guarda o arquivo como veio)."""
        out = Path(tempfile.mkdtemp(prefix='vcw-rec-'))
        self.addCleanup(shutil.rmtree, out, True)
        start, stop = native_call([   # rec_chunk não tem resposta
            {'cmd': 'rec_start', 'title': 'rec', 'ext': 'webm', 'rate': 1, 'out_dir': str(out), 'reqId': 1},
            {'cmd': 'rec_chunk', 'id': 'x', 'data': ''},
            {'cmd': 'rec_stop', 'id': 'x', 'reqId': 2},
        ])
        self.assertTrue(start['ok'])
        rec_id = start['id']
        # gravação com a conexão fechada sem rec_stop: o host salva o que tinha (nada) e marca erro
        jobs = native_call([{'cmd': 'list'}])[0]['jobs']
        job = next(j for j in jobs if j['id'] == rec_id)
        self.assertIn(job['status'], ('error', 'queued', 'done'))
        native_call([{'cmd': 'remove', 'id': rec_id}])


if __name__ == '__main__':
    unittest.main()
