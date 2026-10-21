"""Prepare pinned Geist fonts and locally supplied reference icons for Qt.

Build-time helper only. Installed applications never download theme resources.
"""
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'deepecohab_recorder/assets'
FONT_REVISION = 'a73329da8fc62afc917f796555202e4997f79b7c'  # Geist v1.7.2
ICONS = ('folders', 'download', 'circle-check', 'info-circle', 'circle-x', 'sun', 'moon',
         'users', 'layout-dashboard', 'notes', 'bolt', 'calendar', 'player-play',
         'table', 'grid-dots', 'refresh', 'plus', 'trash', 'search', 'maximize',
         'brand', 'layout-sidebar-left-collapse', 'layout-sidebar-left-expand')


def main():
    resources = []
    for name in ('fonts', 'icons', 'licenses'):
        (ASSETS / name).mkdir(parents=True, exist_ok=True)
    downloads = {
        'fonts/Geist-Regular.ttf': f'https://raw.githubusercontent.com/vercel/geist-font/{FONT_REVISION}/fonts/Geist/ttf/Geist-Regular.ttf',
        'fonts/Geist-SemiBold.ttf': f'https://raw.githubusercontent.com/vercel/geist-font/{FONT_REVISION}/fonts/Geist/ttf/Geist-SemiBold.ttf',
        'fonts/GeistMono-Regular.ttf': f'https://raw.githubusercontent.com/vercel/geist-font/{FONT_REVISION}/fonts/GeistMono/ttf/GeistMono-Regular.ttf',
        'licenses/Geist-OFL.txt': f'https://raw.githubusercontent.com/vercel/geist-font/{FONT_REVISION}/LICENSE.txt',
        'licenses/Tabler-LICENSE.txt': 'https://raw.githubusercontent.com/tabler/tabler-icons/main/LICENSE',
    }
    for name, url in downloads.items():
        request = urllib.request.Request(url, headers={'User-Agent': 'DeepEcoHab-Acquisition-Theme-Setup'})
        with urllib.request.urlopen(request, timeout=25) as response:
            data = response.read()
        if name.endswith('.ttf'):
            assert data[:4] in (b'\x00\x01\x00\x00', b'OTTO'), f'Not a desktop font: {url}'
        else:
            assert b'LICENSE' in data.upper(), f'Not a license: {url}'
        (ASSETS / name).write_bytes(data)
        resources.append(dict(file=name, source=url, sha256=hashlib.sha256(data).hexdigest()))
    reference = ROOT / 'DeepEcoHAB_reference_repository/DeepEcoHab-main/deepecohab/app/assets'
    for name in ICONS:
        source = reference / 'icons' / (name + '.svg')
        target = ASSETS / 'icons' / source.name
        shutil.copyfile(source, target)
        resources.append(dict(file='icons/' + source.name, source='Supplied DeepEcoHab 0.6.0: ' + str(source.relative_to(reference)),
                              sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    (ASSETS / 'theme_resources.json').write_text(json.dumps(dict(geist_release='v1.7.2',
        geist_revision=FONT_REVISION, resources=resources), indent=2) + '\n', encoding='utf-8')
    print(f'Prepared {len(resources)} fonts, icons and notices in {ASSETS}.')


if __name__ == '__main__':
    main()
