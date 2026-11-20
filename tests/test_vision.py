import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from PIL import Image

from imagelens.library import Library
from imagelens.vision import apply_suggestion, check_model, prepare_image, settings_for, suggest, validate_proposal


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.lib = Library(self.root / 'library')
        source = self.root / 'sample.png'; Image.new('RGB', (30, 20), 'red').save(source)
        self.record = self.lib.add(source)
        self.proposal = {'alt_text': 'A red rectangle.', 'tags': ['red', 'rectangle']}
        self.capability = {'capabilities': ['vision', 'completion'], 'details': {'format': 'gguf'}}

    def settings(self): return settings_for('fixture-vision')

    def fake_opener(self, responses):
        opener = Mock(); opener.open.side_effect = [io.BytesIO(json.dumps(r).encode()) for r in responses]
        return patch('imagelens.vision.build_opener', return_value=opener), opener

    def draft(self):
        result = {'done': True, 'message': {'content': json.dumps(self.proposal)}}
        context, opener = self.fake_opener([self.capability, result])
        with context:
            draft = suggest(self.lib, self.record['id'], self.settings())
        return draft, opener

    def test_only_numeric_loopback_and_bounded_settings(self):
        for url in ['https://127.0.0.1', 'http://localhost', 'http://example.com',
                    'http://user@127.0.0.1', 'http://127.0.0.1:0', 'http://127.0.0.1/x',
                    'http://127.0.0.1?x', 'http://127.0.0.1#x']:
            with self.subTest(url=url), self.assertRaises(ValueError): settings_for('vision', url)
        for timeout in [True, 0, float('nan'), 181]:
            with self.assertRaises(ValueError): settings_for('vision', timeout=timeout)
        with self.assertRaises(ValueError): settings_for('provider:cloud')

    def test_text_and_remote_models_refused_before_image_read(self):
        for info in [{'capabilities': ['completion']},
                     {**self.capability, 'remote_host': 'https://example.com'},
                     {'capabilities': ['vision'], 'details': {'format': 'remote'}}]:
            context, opener = self.fake_opener([info])
            with context, patch.object(self.lib, 'read_original') as read, self.assertRaises(ValueError):
                suggest(self.lib, self.record['id'], self.settings())
            read.assert_not_called(); self.assertEqual(opener.open.call_count, 1)

    def test_suggestion_does_not_overwrite_metadata_and_apply_needs_review(self):
        draft, opener = self.draft()
        self.assertEqual(self.lib.get(self.record['id'])['alt_text'], '')
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertTrue(payload['messages'][0]['images'][0])
        applied = apply_suggestion(self.lib, draft)
        self.assertEqual(applied['alt_text'], self.proposal['alt_text'])
        self.assertEqual(applied['metadata_source'], 'vision')
        self.assertEqual(applied['review_status'], 'needs_review')

    def test_stale_draft_cannot_replace_new_manual_metadata(self):
        draft, _ = self.draft()
        self.lib.edit(self.record['id'], 'My reviewed caption.', ['manual'])
        with self.assertRaisesRegex(ValueError, '[Ss]tale|changed'):
            apply_suggestion(self.lib, draft)
        self.assertEqual(self.lib.get(self.record['id'])['alt_text'], 'My reviewed caption.')

    def test_tampered_image_hash_size_and_settings_refused(self):
        draft, _ = self.draft()
        for key, value in [('original_sha256', '0' * 64), ('prepared_sha256', '0' * 64),
                           ('prepared_size', [True, 20]), ('schema_version', True)]:
            changed = copy.deepcopy(draft); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): apply_suggestion(self.lib, changed)
        changed = copy.deepcopy(draft); changed['settings']['options']['temperature'] = False
        with self.assertRaises(ValueError): apply_suggestion(self.lib, changed)
        for capability in [{}, {'model': 'other', 'capabilities': ['vision'], 'format': 'gguf'}]:
            changed = copy.deepcopy(draft); changed['capability'] = capability
            with self.assertRaises(ValueError): apply_suggestion(self.lib, changed)

    def test_prepared_image_strips_exif_and_scales_without_modifying_original(self):
        buffer = io.BytesIO(); exif = Image.Exif(); exif[270] = 'private fixture description'
        Image.new('RGB', (1600, 800), 'blue').save(buffer, format='JPEG', exif=exif)
        raw = buffer.getvalue(); prepared, size = prepare_image(raw)
        self.assertEqual(size, (1024, 512)); self.assertNotIn(b'private fixture', prepared)
        with Image.open(io.BytesIO(prepared)) as image:
            self.assertEqual(image.format, 'PNG'); self.assertFalse(image.getexif())
        self.assertIn(b'private fixture', raw)

    def test_proposal_rejection_and_no_redirect_proxy_transport(self):
        for proposal in [{}, {'alt_text': '', 'tags': []}, {'alt_text': 'x', 'tags': ['Red', 'red']},
                         {'alt_text': 'x', 'tags': 'red'}, {'alt_text': 'x', 'tags': ['x' * 51]}]:
            with self.assertRaises(ValueError): validate_proposal(proposal)
        context, _ = self.fake_opener([self.capability])
        with context as builder: check_model(self.settings())
        handlers = builder.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        with self.assertRaises(ValueError): handlers[1].redirect_request(None, None, 302, None, {}, 'http://example.com')

    def test_incomplete_malformed_and_duplicate_response_refused(self):
        for result in [{'done': False}, {'done': True, 'done_reason': 'length'},
                       {'done': True, 'message': {'content': '{"alt_text":"a","alt_text":"b","tags":[]}'}}]:
            context, _ = self.fake_opener([self.capability, result])
            with context, self.assertRaises(ValueError): suggest(self.lib, self.record['id'], self.settings())
            self.assertEqual(self.lib.get(self.record['id'])['alt_text'], '')

    def test_unavailable_provider_actionable(self):
        with patch('imagelens.vision.build_opener') as builder:
            builder.return_value.open.side_effect = URLError('offline')
            with self.assertRaisesRegex(ValueError, 'unavailable'): check_model(self.settings())


if __name__ == '__main__': unittest.main()
