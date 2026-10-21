"""Generate small Qt SVG indicators from the shared colors, without network access."""
import json
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / 'deepecohab_recorder/assets'


def main():
    tokens = json.loads((ASSETS / 'theme.json').read_text(encoding='utf-8'))
    directory = ASSETS / 'theme'
    directory.mkdir(exist_ok=True)
    for mode in ('light', 'dark'):
        c = tokens[mode]
        for disabled in (False, True):
            suffix = '-disabled' if disabled else ''
            fill, ink = (c['sunken'], c['ink-3']) if disabled else (c['accent'], c['accent-ink'])
            shapes = {
                'checkbox': f'<rect x="1" y="1" width="16" height="16" rx="4" fill="{fill}"/>'
                            f'<path d="M4.5 9l3 3 6-6" fill="none" stroke="{ink}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
                'mixed': f'<rect x="1" y="1" width="16" height="16" rx="4" fill="{fill}"/>'
                         f'<path d="M5 9h8" stroke="{ink}" stroke-width="2" stroke-linecap="round"/>',
                'radio': f'<circle cx="9" cy="9" r="7.5" fill="{c["sunken"] if disabled else c["surface"]}" stroke="{c["line"] if disabled else c["accent"]}"/>'
                         f'<circle cx="9" cy="9" r="4" fill="{c["ink-3"] if disabled else c["accent"]}"/>',
            }
            for name, shape in shapes.items():
                (directory / f'{mode}-{name}{suffix}.svg').write_text(
                    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 18 18">{shape}</svg>\n', encoding='utf-8')
        # Native arrows, with the same round 1.75px strokes as the reference icons.
        for name, path in {'down': 'M6 9l6 6l6-6', 'up': 'M6 15l6-6l6 6',
                           'left': 'M15 6l-6 6l6 6', 'right': 'M9 6l6 6l-6 6'}.items():
            for disabled in ((False, True) if name in ('up', 'down') else (False,)):
                suffix = '-disabled' if disabled else ''
                (directory / f'{mode}-arrow-{name}{suffix}.svg').write_text(
                    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                    f'stroke="{c["ink-3"] if disabled else c["ink-2"]}" stroke-width="1.75" '
                    f'stroke-linecap="round" stroke-linejoin="round"><path d="{path}"/></svg>\n', encoding='utf-8')
    print('Prepared 24 light/dark indicators from theme.json.')


if __name__ == '__main__':
    main()
