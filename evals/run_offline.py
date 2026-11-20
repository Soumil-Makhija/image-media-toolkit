"""Known-answer manual-metadata workflow. This does not evaluate AI labeling."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from imagelens.export import export_gallery, write_json
from imagelens.library import Library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path); args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    metadata = json.loads((ROOT / 'examples/images/metadata.json').read_text())
    results = []
    with tempfile.TemporaryDirectory(prefix='imagelens-eval-') as temp:
        library = Library(Path(temp) / 'library')
        for filename, manual in metadata.items():
            source = ROOT / 'examples/images' / filename
            raw = source.read_bytes(); record = library.add(source)
            edited = library.edit(record['id'], manual['alt_text'], manual['tags'])
            duplicate = library.add(source)
            passed = (record['id'] == hashlib.sha256(raw).hexdigest()
                      and library.read_original(record['id']) == raw and duplicate == edited
                      and edited['review_status'] == 'reviewed' and edited['metadata_source'] == 'manual')
            results.append({'case': filename, 'original_sha256': record['id'], 'passed': passed})
        reopened = Library(library.directory)
        mug = reopened.search('ORANGE mug'); absent = reopened.search('not-present')
        unicode_result = reopened.search('notebook')
        search_passed = len(mug) == 1 and mug[0]['original_filename'] == 'orange-mug.png' and not absent and len(unicode_result) == 1
        results.append({'case': 'persistent-known-answer-search', 'passed': search_passed})
        export_gallery(reopened, args.out / 'gallery')
        write_json(args.out / 'metadata.json', {'schema_version': 1, 'images': reopened.list()})
        results.append({'case': 'gallery-and-metadata-export', 'passed': (args.out / 'gallery/index.html').is_file()})
    summary = {'workflow': 'offline manual metadata', 'results': results,
               'passed': sum(result['passed'] for result in results), 'total': len(results),
               'live_vision_evaluated': False, 'limit': 'Synthetic drawings and human-authored labels test storage/search/export only, not AI image understanding.'}
    write_json(args.out / 'summary.json', summary); print(json.dumps(summary, indent=2))
    return 0 if all(result['passed'] for result in results) else 1


if __name__ == '__main__': raise SystemExit(main())
