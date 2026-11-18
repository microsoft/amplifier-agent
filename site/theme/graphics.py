"""Drawn parts of the Smart Tools theme: icons, tool glyphs, and diagrams."""
import html
from itertools import count

ICONS = {
    'arrow-right': 'M4 12H19.5M13.5 6l6 6-6 6',
    'arrow-up-right': 'M6.5 17.5L17.5 6.5M8 6.5h9.5V16',
    'arrow-down-right': 'M6.5 6.5l11 11M17.5 8v9.5H8',
    'menu': 'M4 9.5H20M4 14.5H20',
    'close': 'M6 6l12 12M18 6L6 18',
    'minus': 'M6 12H18',
    'plus': 'M12 5V19M5 12H19',
    'copy': 'M9 9H20V20H9ZM15 9V4H4V15H9',
    'check': 'M5 12.5l4.5 4.5L19 7.5',
    'search': 'M10.5 4a6.5 6.5 0 1 0 0 13a6.5 6.5 0 1 0 0-13M15.5 15.5L20 20',
    'chat': 'M4 5H20V16H11L7 20V16H4Z',
    'window': 'M3 5H21V19H3ZM3 9.5H21',
    'terminal': 'M3 5H21V19H3ZM7 10l3 2.5L7 15M12.5 15H17',
    'loop': 'M19 12a7 7 0 0 1-12.5 4.3M5 12a7 7 0 0 1 12.5-4.3M17.5 4v3.7h-3.7M6.5 20v-3.7h3.7',
    'spark': 'M12 3l2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2Z',
    'plug': 'M9 3V8M15 3V8M6 8H18V12a6 6 0 0 1-12 0ZM12 18V21',
    'library': 'M12 4l8 4-8 4-8-4ZM4 12l8 4 8-4M4 16l8 4 8-4',
    'box': 'M4 8l8-4 8 4v8l-8 4-8-4ZM4 8l8 4 8-4M12 12v8',
}

# A wrench whose jaw holds a spark.
MARK_BODY = ('M2.3 16.57A22.56 22.56 0 0 0 32.65 44.4L52.25 64A8.31 8.31 0 1 0 64 52.25L44.4 32.65'
             'A22.56 22.56 0 0 0 16.57 2.3L29.15 14.88A10.09 10.09 0 0 1 14.88 29.15Z')
MARK_SPARK = ('M10.68 0C11.54 6.2 15.17 9.83 21.37 10.68C15.17 11.54 11.54 15.17 10.68 21.37'
              'C9.83 15.17 6.2 11.54 0 10.68C6.2 9.83 9.83 6.2 10.68 0Z')
MARK = f'<path d="{MARK_BODY}"/><path d="{MARK_SPARK}"/>'

# A browser tab can be light or dark, and the wrench has no tile behind it.
FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-4 -4 72 72">'
    '<style>path{fill:#181a16}.spark{fill:#80642f}'
    '@media(prefers-color-scheme:dark){path{fill:#f3f0e8}.spark{fill:#b0914d}}</style>'
    f'<path d="{MARK_BODY}"/><path class="spark" d="{MARK_SPARK}"/></svg>')

# One filter serves every pressed glyph on a page.
GLYPH_DEFS = (
    '<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>'
    '<filter id="st-inset" x="-10%" y="-10%" width="120%" height="120%">'
    '<feComponentTransfer in="SourceAlpha"><feFuncA type="table" tableValues="1 0"/></feComponentTransfer>'
    '<feGaussianBlur stdDeviation="2.2"/><feOffset dx="1.5" dy="3" result="shade"/>'
    '<feFlood flood-color="#181a16" flood-opacity=".7"/><feComposite in2="shade" operator="in"/>'
    '<feComposite in2="SourceAlpha" operator="in" result="inner"/>'
    '<feMerge><feMergeNode in="SourceGraphic"/><feMergeNode in="inner"/></feMerge></filter></defs></svg>')

_mask_ids = count(1)


def icon(name, size=20):
    """Line icon on a 24 grid. The stroke scales with the drawn size."""
    return (f'<svg class="icon" viewBox="0 0 24 24" width="{size}" height="{size}" '
            f'stroke-width="{1.5 * 24 / size:.2f}" aria-hidden="true"><path d="{ICONS[name]}"/></svg>')


def mark(class_name='brand-mark'):
    return (f'<svg class="{class_name}" viewBox="-2 -2 68 68" aria-hidden="true">'
            f'<path d="{MARK_BODY}" fill="currentColor"/><path class="mark-spark" d="{MARK_SPARK}"/></svg>')


def _cut(shape, holes):
    """A shape with parts knocked out. A mask id must be unique on its page."""
    identity = f'glyph-cut-{next(_mask_ids)}'
    return (f'<mask id="{identity}"><g fill="#fff">{shape}</g><g fill="#000" stroke="#000" stroke-width="0" '
            f'stroke-linecap="round" stroke-linejoin="round">{holes}</g></mask>'
            f'<rect width="64" height="64" mask="url(#{identity})"/>')


def _line(d, width):
    return f'<path d="{d}" fill="none" stroke-width="{width}"/>'


# Tool glyphs on a 64 unit grid. Each takes its color from the parent fill.
GLYPHS = {
    'deep-research': lambda: _cut('<circle cx="27" cy="27" r="27"/>', '<circle cx="27" cy="27" r="13"/>') + '<path d="M43 51L51 43L62 54A5.6 5.6 0 0 1 54 62Z"/>',
    'fact-check': lambda: _cut('<path d="M8 2H56A8 8 0 0 1 64 10V40A8 8 0 0 1 56 48H31L16 62V48H8A8 8 0 0 1 0 40V10A8 8 0 0 1 8 2Z"/>', _line('M19 26l9 9l18-19', 7)),
    'showrun': lambda: _cut('<rect y="6" width="64" height="52" rx="10"/>', '<path d="M27 21L46 32L27 43Z" stroke-width="4"/>'),
    'unfold': lambda: _cut('<path d="M8 0H38L64 26V56A8 8 0 0 1 56 64H8A8 8 0 0 1 0 56V8A8 8 0 0 1 8 0Z"/>', _line('M38 -2V20A6 6 0 0 0 44 26H66', 5)),
    'outtake': lambda: '<rect y="2" width="37" height="60" rx="8"/><rect x="44" y="14" width="20" height="36" rx="6"/>',
    'stories': lambda: _cut('<rect y="4" width="64" height="56" rx="10"/>', '<rect x="12" y="17" width="26" height="8" rx="4"/><rect x="12" y="32" width="40" height="5" rx="2.5"/><rect x="12" y="43" width="30" height="5" rx="2.5"/>'),
    'possibly': lambda: '<circle cx="16" cy="16" r="16"/><rect x="38" y="2" width="26" height="26" rx="6"/><path d="M32 37L50 62H14Z"/>',
    'tmux': lambda: _cut('<rect y="4" width="64" height="56" rx="10"/>', _line('M30 4V60M30 32H64', 5)),
    'aud': lambda: '<rect x="1" y="20" width="10" height="24" rx="5"/><rect x="14" y="8" width="10" height="48" rx="5"/><rect x="27" width="10" height="64" rx="5"/><rect x="40" y="14" width="10" height="36" rx="5"/><rect x="53" y="22" width="10" height="20" rx="5"/>',
    'vid': lambda: '<rect y="10" width="42" height="44" rx="9"/><path d="M47 27L61 16A2 2 0 0 1 64 18V46A2 2 0 0 1 61 48L47 37Z"/>',
    'hacker-news': lambda: '<path d="M29 6A4 4 0 0 1 35 6L59 34A3 3 0 0 1 57 39H7A3 3 0 0 1 5 34Z"/><rect x="8" y="47" width="48" height="12" rx="6"/>',
    'github-repos': lambda: '<circle cx="14" cy="11" r="11"/><circle cx="14" cy="53" r="11"/><circle cx="50" cy="19" r="11"/><path d="M10.5 11H17.5V33H33A13.5 13.5 0 0 0 46.5 19.5H53.5A20.5 20.5 0 0 1 33 40H17.5V53H10.5Z"/>',
    'gmail': lambda: _cut('<rect y="8" width="64" height="48" rx="9"/>', _line('M9 19L32 37L55 19', 6)),
    'home-assistant': lambda: _cut('<path d="M28 4A6 6 0 0 1 36 4L60 26A6 6 0 0 1 62 30V56A6 6 0 0 1 56 62H8A6 6 0 0 1 2 56V30A6 6 0 0 1 4 26Z"/>', '<rect x="24" y="36" width="16" height="30" rx="4"/>'),
    'lore': lambda: '<path d="M0 12C11 7 22 9 29 15V60C22 54 11 52 0 57Z"/><path d="M64 12C53 7 42 9 35 15V60C42 54 53 52 64 57Z"/>',
    'music-deck': lambda: _cut('<circle cx="32" cy="32" r="32"/>', '<circle cx="32" cy="32" r="8"/>' + _line('M13 32A19 19 0 0 1 32 13', 5)),
    'spotify': lambda: '<ellipse cx="14" cy="51" rx="14" ry="11"/><ellipse cx="50" cy="43" rx="14" ry="11"/><path d="M21 12L64 2V43H57V19L28 26V51H21Z"/>',
    'team-pulse': lambda: _cut('<rect width="64" height="64" rx="12"/>', _line('M7 34H19L27 15L37 49L45 34H57', 6)),
    'workiq': lambda: _cut('<path d="M20 14V9A8 8 0 0 1 28 1H36A8 8 0 0 1 44 9V14H56A8 8 0 0 1 64 22V54A8 8 0 0 1 56 62H8A8 8 0 0 1 0 54V22A8 8 0 0 1 8 14Z"/>', '<rect x="27" y="8" width="10" height="6" rx="1"/>' + _line('M0 36H64', 5)),
    'fast-decisions': lambda: '<path d="M39 0L8 37H28L23 64L56 25H35Z"/>',
}

FIELDS = {
    'deep-research': 'red', 'fact-check': 'slate', 'showrun': 'red', 'unfold': 'yellow',
    'outtake': 'slate', 'stories': 'green', 'possibly': 'blue', 'tmux': 'slate',
    'aud': 'green', 'vid': 'blue', 'hacker-news': 'yellow', 'github-repos': 'slate',
    'gmail': 'red', 'home-assistant': 'green', 'lore': 'blue', 'music-deck': 'red',
    'spotify': 'green', 'team-pulse': 'yellow', 'workiq': 'blue', 'fast-decisions': 'yellow',
}


TILE = '#12161c'


def _stroke(d, color='#eef2f6', cap='square'):
    return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="4.5" stroke-linecap="{cap}"/>'


# A tool's own artwork: a thin line drawing with one pastel part, on an ink tile.
ARTWORK = {
    'reality-check': _stroke('M14 33l10 10 22-23') + '<rect x="44" y="44" width="11" height="11" fill="#f2a7c3"/>',
    'smart-tool-creator': _stroke('M13 13H28V28H13ZM13 36H28V51H13ZM36 36H51V51H36Z') + _stroke('M43.5 13V28M36 20.5H51', '#bfe8d5'),
    'digital-twin-universe': (
        _stroke('M39.5 32.0C39.6 32.5 40.0 33.9 39.9 35.0C39.9 36.0 39.6 37.2 39.1 38.2C38.6 39.2 37.9 40.3 36.9 41.2C36.0 42.0 34.8 42.8 33.5 43.3C32.2 43.7 30.7 44.0 29.3 44.0C27.8 44.0 26.1 43.7 24.7 43.1C23.2 42.5 21.7 41.5 20.5 40.4C19.2 39.2 18.1 37.7 17.3 36.0C16.6 34.4 16.0 32.5 15.9 30.5C15.8 28.6 16.0 26.5 16.6 24.6C17.2 22.6 18.2 20.6 19.5 18.9C20.8 17.2 22.6 15.6 24.5 14.5C26.4 13.3 28.8 12.4 31.1 12.0C33.4 11.6 37.3 12.0 38.5 12.0', cap='round')
        + _stroke('M24.5 32.0C24.4 31.5 24.0 30.1 24.1 29.0C24.1 28.0 24.4 26.8 24.9 25.8C25.4 24.8 26.1 23.7 27.1 22.8C28.0 22.0 29.2 21.2 30.5 20.7C31.8 20.3 33.3 20.0 34.7 20.0C36.2 20.0 37.9 20.3 39.3 20.9C40.8 21.5 42.3 22.5 43.5 23.6C44.8 24.8 45.9 26.3 46.7 28.0C47.4 29.6 48.0 31.5 48.1 33.5C48.2 35.4 48.0 37.5 47.4 39.4C46.8 41.4 45.8 43.4 44.5 45.1C43.2 46.8 41.4 48.4 39.5 49.5C37.6 50.7 35.2 51.6 32.9 52.0C30.6 52.4 26.7 52.0 25.5 52.0', cap='round')
        + '<circle cx="32" cy="32" r="4" fill="#bcdcf7"/>'),
}


def favicon(slug):
    """A tool's own artwork on its tile, or the family wrench."""
    if slug not in ARTWORK:
        return FAVICON
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            f'<rect width="64" height="64" rx="14" fill="{TILE}"/>{ARTWORK[slug]}</svg>')


def field(slug):
    """CSS color of a tool's field."""
    return TILE if slug in ARTWORK else f'var(--field-{FIELDS.get(slug, "fallback")})'


def glyph(slug, name=''):
    """A tool's glyph, or its initial when the theme has none drawn for it."""
    if slug in GLYPHS:
        return GLYPHS[slug]()
    initial = html.escape((name or slug)[:1].upper(), quote=True)
    return (f'<text x="32" y="50" text-anchor="middle" style="font:700 52px var(--sans)">{initial}</text>'
            if initial else MARK)


def pressed(slug, name=''):
    """Glyph pressed tone on tone into a field: a dark recess with a light lower lip."""
    return (f'<g transform="translate(.7 1)" fill="rgb(243 240 232 / .32)">{glyph(slug, name)}</g>'
            f'<g fill="rgb(24 26 22 / .4)" filter="url(#st-inset)">{glyph(slug, name)}</g>')


def tool_art(slug, name=''):
    """What sits on a tool's field: its own artwork, or its glyph pressed in."""
    return ARTWORK.get(slug) or pressed(slug, name)


def flow_diagram(text):
    """Who calls a smart tool, what the tool holds, and what comes back. The wording is the diagram part of text.yaml."""
    def item(name, swatch, row):
        return (f'<div class="flow-item"><span class="tile" style="--swatch:var(--{swatch})">{icon(name, 22)}</span>'
                f'<div><strong>{html.escape(row["name"])}</strong><span>{html.escape(row["text"])}</span></div></div>')

    def part(name, tint, title):
        return f'<div><span class="tile" style="--swatch:var(--tint-{tint})">{icon(name, 22)}</span>{html.escape(title)}</div>'

    def arrow(label):
        return f'<div class="flow-arrow"><span>{html.escape(label)}</span>{icon("arrow-right", 28)}</div>'

    host, tool, caller = text['host'], text['tool'], text['caller']
    hosts = ''.join(item(name, 'pastel-butter', row) for name, row in zip(('chat', 'window', 'terminal'), host['rows'], strict=True))
    parts = ''.join(part(name, tint, title) for (name, tint), title in zip(
        (('loop', 'blue'), ('spark', 'red'), ('plug', 'green'), ('library', 'yellow')), tool['parts'], strict=True))
    results = ''.join(item(name, 'pastel-mint', row) for name, row in zip(('check', 'box'), caller['rows'], strict=True))
    return f'''<div class="flow">
      <div class="flow-panel"><h3>{html.escape(host['title'])}</h3>{hosts}</div>
      {arrow(text['intent'])}
      <div class="flow-panel tool"><h3>{html.escape(tool['title'])}</h3>
        {item('terminal', 'pastel-stone', tool['row'])}
        <div class="flow-core"><p class="eyebrow">{html.escape(tool['core'])}</p>
          <div class="flow-quad">{parts}</div>
        </div>
      </div>
      {arrow(text['result'])}
      <div class="flow-panel"><h3>{html.escape(caller['title'])}</h3>{results}</div>
    </div>
    <p class="dimension">{html.escape(text['footer'])}</p>'''
