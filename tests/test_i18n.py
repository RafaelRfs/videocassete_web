"""Os dois idiomas têm as mesmas mensagens, e toda chave usada no código existe."""
import json
import re
import unittest

from util import ROOT

import i18n

EXT = ROOT / 'extension'


def locale(name):
    return json.loads((EXT / '_locales' / name / 'messages.json').read_text(encoding='utf-8'))


class LocalesTest(unittest.TestCase):
    def test_same_keys_in_extension_locales(self):
        self.assertEqual(set(locale('en')), set(locale('pt_BR')))

    def test_placeholders_match(self):
        en, pt = locale('en'), locale('pt_BR')
        for key in en:
            self.assertEqual(sorted(re.findall(r'\{\d\}', en[key]['message'])),
                             sorted(re.findall(r'\{\d\}', pt[key]['message'])), key)

    def test_every_key_used_exists(self):
        keys = set(locale('en'))
        used = set()
        for js in EXT.glob('*.js'):
            used |= set(re.findall(r"\bt\('([A-Za-z0-9_]+)'", js.read_text(encoding='utf-8')))
        html = (EXT / 'popup.html').read_text(encoding='utf-8')
        used |= set(re.findall(r'data-i18n(?:-title|-placeholder)?="([^"]+)"', html))
        manifest = (EXT / 'manifest.json').read_text(encoding='utf-8')
        used |= set(re.findall(r'__MSG_(\w+)__', manifest))
        for status in ('queued', 'starting', 'analyzing', 'downloading', 'merging', 'done', 'error',
                       'cancelled', 'interrupted', 'recording'):
            used.add('st_' + status)
        self.assertEqual(used - keys, set())

    def test_host_messages(self):
        self.assertEqual(set(i18n.MESSAGES['pt']), set(i18n.MESSAGES['en']))
        for lang in ('pt', 'en'):
            for key, text in i18n.MESSAGES[lang].items():
                self.assertEqual(set(re.findall(r'\{(\w+)\}', text)),
                                 set(re.findall(r'\{(\w+)\}', i18n.MESSAGES['en'][key])), key)

    def test_host_keys_used_exist(self):
        used = set()
        for py in (ROOT / 'host').glob('*.py'):
            used |= set(re.findall(r"\btr\('([a-z0-9_]+)'", py.read_text(encoding='utf-8')))
        self.assertEqual(used - set(i18n.MESSAGES['en']), set())

    def test_set_lang(self):
        i18n.set_lang('pt-BR')
        self.assertIn('ao vivo', i18n.tr('live'))
        i18n.set_lang('en-US')
        self.assertIn('live', i18n.tr('live'))


if __name__ == '__main__':
    unittest.main()
