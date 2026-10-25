import ast
import sys
import unittest
from unittest.mock import patch

from paths import ROOT

sys.path.insert(0, str(ROOT / 'launcher'))
import uncharted_i18n


class LauncherTranslationTests(unittest.TestCase):
    def test_each_language_translates_play(self):
        expected = {
            'es': 'Jugar', 'pt': 'Jogar', 'fr': 'Jouer', 'de': 'Spielen',
            'it': 'Gioca', 'ja': 'プレイ',
        }
        for language, translation in expected.items():
            with self.subTest(language=language):
                self.assertEqual(uncharted_i18n.translate(language, 'Play'), translation)

    def test_missing_text_falls_back_to_english(self):
        self.assertEqual(uncharted_i18n.translate('es', 'Untranslated text'), 'Untranslated text')
        self.assertEqual(uncharted_i18n.translate('en', 'Play'), 'Play')

    def test_automatic_language_uses_windows_locale(self):
        for language in ('es', 'pt', 'fr', 'de', 'it', 'ja'):
            with self.subTest(language=language), patch.object(
                    uncharted_i18n.locale, 'getlocale', return_value=(f'{language}_XX', 'UTF-8')):
                self.assertEqual(uncharted_i18n.language_code(''), language)
        with patch.object(uncharted_i18n.locale, 'getlocale', return_value=('ko_KR', 'UTF-8')):
            self.assertEqual(uncharted_i18n.language_code(''), 'en')

    def test_launcher_offers_selected_languages(self):
        self.assertEqual(
            [code for code, _name in uncharted_i18n.LANGUAGES],
            ['', 'en', 'es', 'pt', 'fr', 'de', 'it', 'ja'],
        )

    def test_interface_language_help_is_translated(self):
        text = 'This changes only the launcher. The game language is selected on the Game page.'
        self.assertNotEqual(uncharted_i18n.translate('es', text), text)

    def test_two_destination_layout_strings_have_real_translations(self):
        """New Play | Settings labels are translated, not English fallbacks or format breaks."""
        import collections
        import string

        def fields(text):
            return collections.Counter((field, spec, conversion)
                                       for _, field, spec, conversion in string.Formatter().parse(text)
                                       if field is not None)

        languages = ('es', 'pt', 'fr', 'de', 'it', 'ja')
        expected = set(uncharted_i18n.LAYOUT_TRANSLATIONS['es'])
        self.assertIn('Settings', expected)
        self.assertIn('Save settings', expected)
        for language in languages:
            with self.subTest(language=language):
                table = uncharted_i18n.LAYOUT_TRANSLATIONS[language]
                self.assertEqual(set(table), expected)
                for text, translation in table.items():
                    with self.subTest(text=text):
                        self.assertNotEqual(translation, text)
                        self.assertEqual(fields(text), fields(translation))
                        self.assertEqual(uncharted_i18n.translate(language, text), translation)

    def test_each_translation_covers_every_launcher_text(self):
        tree = ast.parse((ROOT / 'launcher' / 'uncharted_launcher.py').read_text(encoding='utf-8'))
        texts = {node.args[0].value for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == '_' and node.args
                 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)}
        for language in ('es', 'pt', 'fr', 'de', 'it', 'ja'):
            with self.subTest(language=language):
                self.assertEqual(texts - set(uncharted_i18n.TRANSLATIONS[language]), set())


class LauncherTranslationReadinessTests(unittest.TestCase):
    def test_enumerated_gaps_are_translated_not_english_fallbacks(self):
        import json
        inventory = json.loads((ROOT / 'docs/diagnostics-translation-gaps.json').read_text(encoding='utf-8'))
        for language, texts in inventory['missing_before_fix'].items():
            for text in texts:
                with self.subTest(language=language, text=text):
                    self.assertIn(text, uncharted_i18n.TRANSLATIONS[language])
                    self.assertNotEqual(uncharted_i18n.translate(language, text), text)

    def test_all_launcher_translation_format_fields_are_preserved(self):
        import collections
        import string
        tree = ast.parse((ROOT / 'launcher/uncharted_launcher.py').read_text(encoding='utf-8'))
        texts = {node.args[0].value for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == '_' and node.args
                 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)}
        def fields(text):
            return collections.Counter((field, spec, conversion)
                                       for _, field, spec, conversion in string.Formatter().parse(text)
                                       if field is not None)
        for language in ('es', 'pt', 'fr', 'de', 'it', 'ja'):
            for text in texts:
                with self.subTest(language=language, text=text):
                    self.assertIn(text, uncharted_i18n.TRANSLATIONS[language])
                    self.assertEqual(fields(text), fields(uncharted_i18n.TRANSLATIONS[language][text]))


if __name__ == '__main__':
    unittest.main()
