import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from imagelens.cli import main
from imagelens.export import export_gallery, write_json
from imagelens.library import Library


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.lib = Library(self.root / 'library')
        source = self.root / '<test>.png'; Image.new('RGBA', (40, 20), 'blue').save(source)
        self.raw = source.read_bytes(); self.record = self.lib.add(source)
        self.lib.edit(self.record['id'], '<script>caption</script> "quoted"', ['blue', '<tag>'])

    def invoke(self, args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_gallery_escapes_metadata_and_preserves_original(self):
        destination = self.root / 'gallery'; export_gallery(self.lib, destination)
        text = (destination / 'index.html').read_text()
        self.assertIn('&lt;script&gt;caption&lt;/script&gt;', text)
        self.assertNotIn('<script>', text); self.assertIn('Content-Security-Policy', text)
        self.assertIn('src="data:image/png;base64,', text)
        manifest = json.loads((destination / 'metadata.json').read_text())
        preview = destination / manifest['images'][0]['preview_path']
        with Image.open(preview) as image: self.assertEqual(image.size, (40, 20))
        self.assertEqual(self.lib.read_original(self.record['id']), self.raw)

    def test_gallery_refuses_existing_and_cleans_owned_partial_export(self):
        destination = self.root / 'gallery'; destination.mkdir(); keep = destination / 'keep'; keep.write_text('retained')
        with self.assertRaises(ValueError): export_gallery(self.lib, destination)
        self.assertEqual(keep.read_text(), 'retained')
        new = self.root / 'new'
        with patch.object(self.lib, 'read_original', side_effect=ValueError('corrupt')), self.assertRaises(ValueError):
            export_gallery(self.lib, new)
        self.assertFalse(new.exists())

    def test_gallery_search_and_empty_export(self):
        destination = self.root / 'empty'; export_gallery(self.lib, destination, query='not-present')
        self.assertEqual(json.loads((destination / 'metadata.json').read_text())['images'], [])

    def test_json_no_clobber_and_broken_symlink_refusal(self):
        path = self.root / 'meta.json'; write_json(path, {'name': 'café'})
        with self.assertRaises(ValueError): write_json(path, {'changed': True})
        self.assertEqual(json.loads(path.read_text()), {'name': 'café'})
        path = self.root / 'link'; path.symlink_to(self.root / 'absent')
        with self.assertRaises(ValueError): write_json(path, {})

    def test_cli_search_export_original_and_refusal_before_inference(self):
        prefix = ['--library', str(self.lib.directory)]
        code, out, _ = self.invoke(prefix + ['search', 'BLUE'])
        self.assertEqual(code, 0); self.assertEqual(json.loads(out)[0]['id'], self.record['id'])
        target = self.root / 'original.bin'
        code, _, _ = self.invoke(prefix + ['export-original', self.record['id'], '--out', str(target)])
        self.assertEqual(code, 0); self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), self.record['id'])
        with patch('imagelens.cli.suggest') as suggest:
            code, _, err = self.invoke(prefix + ['suggest', self.record['id'], '--model', 'fixture', '--out', str(target)])
        self.assertEqual(code, 2); self.assertIn('already exists', err); suggest.assert_not_called()

    def test_cli_requires_explicit_library(self):
        code, _, error = self.invoke(['list'])
        self.assertEqual(code, 2); self.assertIn('--library', error)


if __name__ == '__main__': unittest.main()
