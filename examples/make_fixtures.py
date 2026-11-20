"""Draw three synthetic image fixtures; no model or external image source."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

METADATA = {
    "orange-mug.png": {"alt_text": "An orange mug on a cream background.", "tags": ["orange", "mug", "cream"]},
    "blue-notebook.jpg": {"alt_text": "A blue notebook with white page lines.", "tags": ["blue", "notebook", "paper"]},
    "green-leaf.webp": {"alt_text": "A green leaf with a thin stem on a pale background.", "tags": ["green", "leaf", "stem"]},
}


def make_fixtures(destination):
    destination = Path(destination); destination.mkdir(parents=True, exist_ok=False)
    image = Image.new('RGB', (360, 260), '#f9f2e5'); draw = ImageDraw.Draw(image)
    draw.ellipse((220, 92, 295, 182), outline='#b86738', width=15)
    draw.rounded_rectangle((88, 62, 240, 205), radius=20, fill='#ce7944')
    draw.ellipse((88, 52, 240, 85), fill='#e29d66', outline='#a95731', width=4)
    image.save(destination / 'orange-mug.png')
    image = Image.new('RGB', (360, 260), '#eef2f5'); draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((90, 35, 265, 225), radius=10, fill='#315d9a')
    draw.rectangle((106, 44, 115, 215), fill='#183c70')
    for y in (95, 117, 139, 161): draw.line((140, y, 235, y), fill='#dbe6f2', width=4)
    image.save(destination / 'blue-notebook.jpg', quality=92)
    image = Image.new('RGB', (360, 260), '#edf4e9'); draw = ImageDraw.Draw(image)
    draw.ellipse((105, 45, 255, 195), fill='#56865d')
    draw.line((115, 220, 205, 85), fill='#2d5639', width=7)
    draw.line((155, 160, 140, 112), fill='#a8c393', width=3)
    draw.line((177, 128, 225, 115), fill='#a8c393', width=3)
    image.save(destination / 'green-leaf.webp', lossless=True)
    (destination / 'metadata.json').write_text(json.dumps(METADATA, indent=2) + '\n')
    return METADATA


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('destination', type=Path)
    print(json.dumps(make_fixtures(parser.parse_args().destination), indent=2))
