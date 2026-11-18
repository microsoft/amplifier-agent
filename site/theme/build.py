#!/usr/bin/env python3
"""Build a standalone Smart Tools page. Theme v0.3.0, MIT licensed."""
import argparse
import html
import json
import re
import shutil
import unicodedata
from datetime import date
from itertools import cycle
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import yaml

from catalog_metadata import (
    catalog_sources, load_catalog_metadata, read_json, read_snapshot,
    recommendation_state, validate_pointer,
)
from graphics import (
    ARTWORK, GLYPH_DEFS, favicon, field, flow_diagram, icon, mark, tool_art,
)

THEME = Path(__file__).resolve().parent
FAMILY = json.loads((THEME / 'family.json').read_text())
PAGES = FAMILY['pages']
SPEC = 'https://github.com/microsoft/amplifier-smart-tools/tree/main/spec'
PLATFORM_LABELS = {'linux': 'Linux', 'macos': 'macOS', 'windows': 'Windows'}
UNCLASSIFIED = '__unclassified__'
TEXT = yaml.safe_load((THEME / 'text.yaml').read_text(encoding='utf-8'))
SHARED, HOME, STORE, TOOL, BLOG, READER, LLMS, TOOL_SITE = (
    TEXT[part] for part in ('shared', 'overview', 'catalog', 'tool_page', 'blog', 'specification', 'llms', 'tool_site'))
FAMILY_PROMPT = SHARED['prompt']
BENEFITS = [(item['title'], item['text']) for item in HOME['why']['items']]
ABOUT = HOME['what']['text']
BUILD_STEPS = HOME['build']['steps']
CHAPTERS = list(READER['chapters'].items())


def plain(value):
    """Use plain punctuation in displayed metadata without editing source snapshots."""
    text = str(value).translate(str.maketrans({'\u2014': ' - ', '\u2013': '-', '\u2019': "'", '\u2018': "'", '\u201c': '"', '\u201d': '"', '\u2026': '...', '\u00b7': '/', '\u00d7': 'x', '\u2192': ' to '}))
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()


def esc(value):
    return html.escape(plain(value), quote=True)


def words(value):
    """Wording from text.yaml as page content."""
    return html.escape(plain(value), quote=False)


def lines(value):
    """A heading whose entries are its lines."""
    return '<br>'.join(words(line) for line in value)


def safe_url(value):
    parsed = urlsplit(str(value))
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('Expected a credential-free HTTPS URL')
    return str(value)


def link(url, text, classes=''):
    return f'<a class="{classes}" href="{html.escape(url, quote=True)}">{esc(text)}</a>'


def repo_url(key):
    p = PAGES[key]
    return f'https://github.com/{p["owner"]}/{p["repository"]}'


def page_url(key, args):
    p = PAGES[key]
    if args.local:
        return f'/{p["repository"]}/'
    owner = args.family_owner if p['owner'] == 'microsoft' and args.family_owner else p['owner']
    return f'https://{owner}.github.io/{p["repository"]}/'


def button(label, url, variant='', arrow=''):
    classes = f'button {variant}'.strip()
    return f'<a class="{classes}" href="{html.escape(url, quote=True)}">{esc(label)}{icon("arrow-" + arrow, 18) if arrow else ""}</a>'


def text_link(label, url, arrow='up-right'):
    return f'<a class="text-link" href="{html.escape(url, quote=True)}">{esc(label)}{icon("arrow-" + arrow, 16) if arrow else ""}</a>'


def get_link(name, url):
    """Compact link to a tool's page."""
    return f'<a class="get" href="{html.escape(url, quote=True)}" aria-label="{esc(SHARED["get"])} {esc(name)}">{words(SHARED["get"])}</a>'


def code_box(text, title, identity):
    # A long address may break after a slash, so a narrow box does not cut a word in two. The copied text is the same.
    shown = re.sub(r'(?<![:/])/(?=\w)', '/<wbr>', esc(text))
    return f'''<div class="copy-region"><div class="code-box"><div class="code-head"><span>{esc(title)}</span><button class="copy" type="button" data-copy="{identity}" aria-label="{esc(SHARED['copy'])} {esc(title)}">{icon('copy', 14)}{words(SHARED['copy'])}</button></div><pre id="{identity}">{shown}</pre></div><p class="status-message" role="status" aria-live="polite"></p></div>'''


def app_icon(slug, name):
    classes = 'app-icon art' if slug in ARTWORK else 'app-icon'
    return f'<span class="{classes}" style="--field:{field(slug)}"><svg viewBox="0 0 64 64" aria-hidden="true">{tool_art(slug, plain(name))}</svg></span>'


def section_head(title, text=''):
    return f'<div class="section-head"><h2>{title}</h2>{f"<p>{esc(text)}</p>" if text else ""}</div>'


def numbered(steps):
    return '<ol class="numbered">' + ''.join(f'<li><h3>{esc(t)}</h3>{f"<p>{esc(p)}</p>" if p else ""}</li>' for t, p in steps) + '</ol>'


def reasons(items, columns=4):
    tiles = zip(items, cycle(('blue', 'green', 'yellow', 'red')))
    return f'<div class="benefits cards{"" if columns == 4 else f" of-{columns}"}">' + ''.join(
        f'<div><span class="tile" style="--swatch:var(--tint-{tint})">{icon(item["icon"], 22)}</span>'
        f'<strong>{esc(item["title"])}</strong><p>{esc(item["text"])}</p></div>' for item, tint in tiles) + '</div>'


def get_started(prompt, note=''):
    """Prompt a reader hands to an agent. The note is trusted markup."""
    note = f'<p class="note">{note}</p>' if note else ''
    return f'<div class="callout"><h3>{words(SHARED["ask"])}</h3>{code_box(prompt, SHARED["prompt_label"], "prompt")}{note}</div>'


def facts_row(facts):
    """A tool's facts as one quiet line. Each term stays available to screen readers."""
    return '<dl class="facts">' + ''.join(f'<div><dt class="visually-hidden">{esc(term)}</dt><dd>{value}</dd></div>' for term, value in facts) + '</dl>'


def tool_card(name, slug, tagline, url):
    classes = 'tool-card wide art' if slug in ARTWORK else 'tool-card wide'
    return f'''<article class="{classes}" style="--field:{field(slug)}" data-tool="{esc(slug)}"><svg class="tool-card-art" viewBox="0 0 64 64" aria-hidden="true">{tool_art(slug, plain(name))}</svg><div class="tool-card-top"></div><div class="tool-card-body"><div><h3 class="tool-card-title"><a href="{html.escape(url, quote=True)}">{esc(name)}</a></h3><p class="tool-card-sub">{esc(tagline)}</p></div>{get_link(name, url)}</div></article>'''


def app_row(tool, base):
    """One line of a tool list: icon, name, one sentence, Get."""
    url = f'{base}{tool["slug"]}/'
    marker = f'<span class="tag">{words(SHARED["recommended"])}</span>' if tool['state'] == 'recommended' else ''
    review = f'<span class="tag">{words(SHARED["needs_review"])}</span>' if tool['state'] == 'needs-review' else ''
    return f'''<article class="app-row" data-tool="{esc(tool['slug'])}" data-category="{esc(tool['category'] or UNCLASSIFIED)}" data-search="{tool['search']}">{app_icon(tool['slug'], tool['name'])}<div><h3 class="app-row-name"><a href="{url}">{esc(tool['name'])}</a>{marker}</h3><p>{esc(tool['tagline'])}</p>{review}</div>{get_link(tool['name'], url)}</article>'''


def related(keys, args):
    rows = ''
    for key in keys:
        name, url = PAGES[key]['name'], page_url(key, args)
        tagline = f'<p>{esc(PAGES[key]["tagline"])}</p>' if PAGES[key].get('tagline') else ''
        rows += f'<article class="app-row">{app_icon(key, name)}<div><h3 class="app-row-name"><a href="{url}">{esc(name)}</a></h3>{tagline}</div>{get_link(name, url)}</article>'
    return f'<div class="app-list">{rows}</div>'


def first_sentence(text):
    text = ' '.join(str(text).split())
    match = re.match(r'.+?[.!?](?=\s|$)', text)
    return match.group(0) if match else text


def news(config, root, catalog_url):
    """Newest entries from site.json and the blog. On one date, a site.json entry comes first."""
    items = []
    for entry in config.get('news', []):
        slug = entry.get('tool')
        if slug is not None and not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', str(slug)):
            raise ValueError('News tool must be a catalog directory name')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(entry['date'])):
            raise ValueError('News date must be written as YYYY-MM-DD')
        items.append((date.fromisoformat(entry['date']), entry['label'], entry['title'], f'{catalog_url}tools/{slug}/' if slug else ''))
    items += [(post['date'], HOME['news']['blog'], post['title'], f'./blog/{post["slug"]}/') for post in blog_posts(root)]
    items.sort(key=lambda item: item[0], reverse=True)
    rows = ''
    for day, label, title, url in items[:5]:
        title = f'<a href="{html.escape(url, quote=True)}">{esc(title)}{icon("arrow-right", 16)}</a>' if url else esc(title)
        rows += f'<li><p class="news-meta"><time datetime="{day.isoformat()}">{day:%b} {day.day}, {day.year}</time><span class="tag">{esc(label)}</span></p><p class="news-title">{title}</p></li>'
    return f'<aside class="news" aria-labelledby="news-title"><h2 id="news-title">{words(HOME["news"]["title"])}</h2><ol class="news-list">{rows}</ol></aside>' if rows else ''


def overview(config, args, root):
    catalog_url = page_url('catalog', args)
    cards = ''
    for item in config.get('featured', []):
        if not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', item['slug']):
            raise ValueError('Featured slug must be a catalog directory name')
        cards += tool_card(item['name'], item['slug'], item['tagline'], f'{catalog_url}tools/{item["slug"]}/')
    featured = f'''<section class="section">{section_head(words(HOME['featured']['title']))}<div class="card-grid three">{cards}</div><div class="actions">{text_link(HOME['featured']['all'], catalog_url, 'right')}</div></section>''' if cards else ''
    hero, build = HOME['hero'], HOME['build']
    return f'''<section class="hero"><div class="hero-grid"><div>
      <h1>{lines(hero['headline'])}</h1>
      <div class="hero-start" id="get-started"><p class="eyebrow">{words(hero['eyebrow'])}</p>{code_box(FAMILY_PROMPT, SHARED['prompt_label'], 'prompt')}</div>
      <div class="actions">{button(hero['catalog'], catalog_url, 'action', 'right')}{text_link(hero['build'], '#build', 'down-right')}</div>
    </div>{news(config, root, catalog_url)}</div></section>
    {featured}
    <section class="section" id="why">{section_head(words(HOME['why']['title']))}{reasons(HOME['why']['items'])}<div class="build" id="build"><div><h3>{lines(build['title'])}</h3><div class="actions">{text_link(build['specification'], SPEC)}{text_link(build['conformance'], repo_url('overview') + '/tree/main/conformance')}</div></div>{numbered((step, '') for step in BUILD_STEPS)}</div></section>
    <section class="section" id="what">{section_head(words(HOME['what']['title']))}<p class="about">{esc(ABOUT)}</p>{flow_diagram(TEXT['diagram'])}</section>'''


def requests(title, items):
    """Things to ask an agent, each with its own copy button."""
    rows = ''.join(
        f'<li class="copy-region"><p id="request-{number}">{esc(item)}</p>'
        f'<button class="copy-request" type="button" data-copy="request-{number}" aria-label="{esc(SHARED["copy"])}">{icon("copy", 16)}{icon("check", 16)}</button>'
        f'<p class="visually-hidden" role="status" aria-live="polite"></p></li>' for number, item in enumerate(items, 1))
    return f'<aside class="news requests" aria-labelledby="requests-title"><h2 id="requests-title">{esc(title)}</h2><ul class="news-list">{rows}</ul></aside>'


def manifest_facts(root):
    """Version and platforms from the tool's own manifest, so the page follows each release."""
    pointer = root/'smart-tool.json'
    if not pointer.is_file():
        return []
    manifest = (root/json.loads(pointer.read_text(encoding='utf-8'))['manifest']).read_text(encoding='utf-8')
    meta = yaml.safe_load(manifest.split('---')[1])
    facts = [('Version', f'v{esc(meta["version"])}')] if meta.get('version') else []
    if meta.get('platforms'):
        facts.append(('Platform', esc(', '.join(PLATFORM_LABELS.get(name, name) for name in meta['platforms']))))
    return facts


def side_links(links, repo):
    """Links beside a block. A path is a file in the tool's repository."""
    return ''.join(text_link(item['label'], item.get('url') or f'{repo}/blob/main/{item["path"]}') for item in links)


def heading(part):
    """A section's heading, with the sentence beside it when it has one."""
    return section_head(esc(part['title']), part.get('text', '')) if part.get('title') else ''


def pairs(items):
    """Entries written as a title, or as a title and a text."""
    return [(item, '') if isinstance(item, str) else tuple(item) for item in items]


def figure(media, caption, actions='', zoom=False):
    """A picture or video. Without a caption it stands alone, with no row of links under it."""
    actions = f'<span class="demo-actions">{actions}</span>' if actions else ''
    under = f'<figcaption class="image-caption"><span>{esc(caption)}</span>{actions}</figcaption>' if caption else ''
    return f'<figure><div class="product-image{" zoom" if zoom else ""}">{media}</div>{under}</figure>'


def media_section(part, repo, name):
    """A picture or a looping video of the tool. With a title it is a section of its own."""
    image = './assets/' + Path(part['image']).name
    size = f'width="{part["width"]}" height="{part["height"]}"'
    if part.get('video'):
        video = './assets/' + Path(part['video']).name
        player = f'<video data-demo-video controls muted loop playsinline preload="metadata" poster="{image}" {size} aria-label="{esc(part["alt"])}"><source src="{video}" type="video/mp4">{link(video, TOOL_SITE["watch"])}</video>'
        shown = figure(player, part.get('caption'), f'<button class="motion-toggle" data-motion-toggle hidden type="button" aria-pressed="false">{words(TOOL_SITE["pause"])}</button> {link(video, TOOL_SITE["watch_full_size"])}')
    else:
        shown = figure(f'<a href="{image}" aria-label="Open full-size {esc(name)} image"><img src="{image}" alt="{esc(part["alt"])}" {size}></a>', part.get('caption'), link(image, TOOL_SITE['full_size']), part.get('zoom', False))
    if not part.get('title'):
        return shown
    return f'<section class="section"{anchor(part)}>{heading(part)}{shown}</section>'


def anchor(part):
    return f' id="{esc(part["id"])}"' if part.get('id') else ''


def band(part, repo):
    """Numbered steps on a dark band, beside a heading and its links."""
    title = part.get('title', TOOL_SITE['how'])
    links = f'<div class="actions">{side_links(part["links"], repo)}</div>' if part.get('links') else ''
    return f'<div class="build"><div><h3>{esc(title) if isinstance(title, str) else lines(title)}</h3>{links}</div>{numbered(pairs(part["items"]))}</div>'


def steps_section(part, repo, name):
    return f'<section class="section joined"{anchor(part)}>{band(part, repo)}</section>'


def cards_section(part, repo, name):
    """Cards under an icon tile. Steps given with the cards sit on a band under them."""
    cards = [item if isinstance(item, dict) else dict(zip(('title', 'text'), item), icon=shape)
             for item, shape in zip(part.get('items', []), cycle(('box', 'plug', 'window', 'check')))]
    under = band(part['steps'], repo) if part.get('steps') else ''
    return f'<section class="section"{anchor(part)}>{heading(part)}{reasons(cards, len(cards)) if cards else ""}{under}</section>'


def report_section(part, repo, name):
    """An example of what a check returns: each finding, its state, and its evidence."""
    marks = {'passed': 'check', 'failed': 'close', 'skipped': 'minus'}
    labels = TOOL_SITE['states']
    rows = ''
    for finding in part['findings']:
        state = finding['state']
        text = f'<p>{esc(finding["text"])}</p>' if finding.get('text') else ''
        evidence = ''.join(f'<li>{esc(item)}</li>' for item in finding.get('evidence', []))
        evidence = f'<ul class="evidence">{evidence}</ul>' if evidence else ''
        rows += (f'<li class="finding {state}"><span class="state">{icon(marks[state], 18)}</span>'
                 f'<div><p class="finding-state">{words(labels[state])}</p><h3>{esc(finding["title"])}</h3>{text}</div>{evidence}</li>')
    counts = [(state, sum(finding['state'] == state for finding in part['findings'])) for state in marks]
    tally = ''.join(f'<li class="{state}">{count} {words(labels[state]).lower()}</li>' for state, count in counts if count)
    caption = f'<p class="image-caption">{esc(part["caption"])}</p>' if part.get('caption') else ''
    return (f'<section class="section"{anchor(part)}>{heading(part)}<div class="report"><div class="report-head">'
            f'<div><p class="eyebrow">{words(TOOL_SITE["request"])}</p><p class="report-request">{esc(part["request"])}</p></div>'
            f'<ul class="tally">{tally}</ul></div><ol class="findings">{rows}</ol></div>{caption}</section>')


def embed_section(part, repo, name):
    """A working part of the tool in a frame. The build copies its directory and files beside the page."""
    frame = (f'<iframe class="embed" src="./{quote(Path(part["directory"]).name)}/" title="{esc(part["label"])}" '
             f'height="{int(part.get("height", 600))}" loading="lazy"></iframe>')
    return f'<section class="section"{anchor(part)}>{heading(part)}{figure(frame, part.get("caption"))}</section>'


def embed_sources(root, part):
    """An embed's directory and its other files, each in the repository."""
    sources = [(root/path).resolve() for path in (part['directory'], *part.get('files', []))]
    if not all(source.is_relative_to(root) for source in sources):
        raise ValueError('Embedded files must belong to the repository')
    return sources[0], sources[1:]


def enclosure_section(part, repo, name):
    """What sits inside a boundary, and how the outside reaches it."""
    def row(item):
        return (f'<div class="flow-item"><span class="tile">{icon(item["icon"], 22)}</span>'
                f'<div><strong>{esc(item["name"])}</strong><span>{esc(item["text"])}</span></div></div>')

    outside, inside = part['outside'], part['inside']
    footer = f'<p class="dimension">{esc(part["footer"])}</p>' if part.get('footer') else ''
    return (f'<section class="section"{anchor(part)}>{heading(part)}<div class="flow enclosure">'
            f'<div class="flow-panel"><h3>{esc(outside["title"])}</h3>{"".join(row(item) for item in outside["rows"])}</div>'
            f'<div class="flow-arrow"><span>{esc(part["link"])}</span>{icon("arrow-right", 28)}</div>'
            f'<div class="flow-panel wall"><h3>{esc(inside["title"])}</h3><p class="eyebrow">{esc(inside["note"])}</p>'
            f'<div class="enclosure-parts">{"".join(row(item) for item in inside["parts"])}</div></div></div>{footer}</section>')


def blocks_section(part, repo, name):
    """The parts of what the tool makes, as blocks. One block is the reader's own part."""
    blocks = ''.join(
        f'<div class="block{" own" if item.get("own") else ""}">{icon("plus", 44) if item.get("own") else ""}'
        f'<h3>{esc(item["title"])}</h3><p>{esc(item["text"])}</p></div>' for item in part['items'])
    text = f'<p>{esc(part["text"])}</p>' if part.get('text') else ''
    return f'<section class="section split"{anchor(part)}><div><h2>{esc(part["title"])}</h2>{text}</div><div class="blocks">{blocks}</div></section>'


def fit_section(part, repo, name):
    """What the tool is for, beside what it is not for."""
    def column(side, mark, classes):
        rows = ''.join(f'<li>{icon(mark, 18)}<span>{esc(item)}</span></li>' for item in side['items'])
        note = f'<p class="note">{esc(side["note"])}</p>' if side.get('note') else ''
        return f'<div class="{classes}"><h3>{esc(side["title"])}</h3><ul>{rows}</ul>{note}</div>'

    return f'<section class="section"{anchor(part)}>{heading(part)}<div class="fit">{column(part["use"], "check", "fit-list use")}{column(part["other"], "minus", "fit-list")}</div></section>'


SECTION_KINDS = {
    'media': media_section, 'cards': cards_section, 'steps': steps_section, 'report': report_section,
    'embed': embed_section, 'enclosure': enclosure_section, 'blocks': blocks_section, 'fit': fit_section,
}


def tool_sections(config):
    """The page's sections. A site that lists none gets a picture, cards over a band of steps, and its demos."""
    if 'sections' in config:
        return config['sections']
    parts = [dict(config, kind='media', title='')] if config.get('image') else []
    steps = {'items': config['steps'], 'links': config.get('step_links', []), **({'title': config['flow_title']} if config.get('flow_title') else {})} if config.get('steps') else None
    if config.get('details') or steps:
        parts.append({'kind': 'cards', 'id': 'features', 'title': config.get('detail_title', ''), 'items': config.get('details', []), 'steps': steps})
    parts += [dict(item, kind='media', text=item['description']) for item in config.get('capability_demos', [])]
    return parts


def tool_page(config, args, root):
    key, name = config['key'], config['name']
    repo = repo_url(key)
    promise = config['promise']
    headline = esc(promise) if isinstance(promise, str) else lines(promise)
    facts = manifest_facts(root)
    identity = f'<div class="tool-id">{app_icon(key, name)}<div><p class="tool-name">{esc(name)}</p>{facts_row(facts) if facts else ""}</div></div>'
    standfirst = f'<p class="standfirst">{esc(config["standfirst"])}</p>' if config.get('standfirst') else ''
    note = ''
    if config.get('prerequisites'):
        guide = f' {link(repo + "/blob/main/" + config["guide"], TOOL_SITE["guide"])}' if config.get('guide') else ''
        note = f'<p class="note">{esc(config["prerequisites"])}{guide}</p>'
    prompt = config.get('prompt') or SHARED['tool_prompt'].format(url=repo)
    asks = config.get('requests')
    asked = requests(asks.get('title', TOOL_SITE['requests']), asks['items']) if asks else ''
    hero = f'''<section class="hero"><div class="hero-grid"><div>
      {identity}<h1>{headline}</h1>{standfirst}
      <div class="actions">{button(SHARED['github'], repo, 'action', 'up-right')}{side_links(config.get('links', []), repo)}</div>
    </div><div class="hero-side"><div class="hero-start" id="get-started"><p class="eyebrow">{words(TOOL_SITE['install'])}</p>{code_box(prompt, SHARED['prompt_label'], 'prompt')}{note}</div>{asked}</div></div></section>'''
    body = ''.join(SECTION_KINDS[part['kind']](part, repo, name) for part in tool_sections(config))
    more = f'<section class="section">{section_head(words(SHARED["more_tools"]))}{related(config["related"], args)}</section>' if config.get('related') else ''
    tint = config.get('tint', '')
    if tint and not re.fullmatch(r'#[0-9a-fA-F]{6}', tint):
        raise ValueError('Tint must be a hex color')
    shell = f'<div class="tool-site tinted" style="--tint:{tint}">' if tint else '<div class="tool-site">'
    return f'{shell}{hero}{body}{more}</div>'


def section_points(part):
    """A section's heading and points as plain text."""
    kind = part['kind']
    if kind == 'cards':
        cards = [item if isinstance(item, dict) else dict(zip(('title', 'text'), item)) for item in part.get('items', [])]
        return part.get('title'), [f'{card["title"]}: {card["text"]}' for card in cards]
    if kind == 'steps':
        title = part.get('title', TOOL_SITE['how'])
        return title if isinstance(title, str) else ' '.join(title), [' '.join(filter(None, pair)) for pair in pairs(part['items'])]
    if kind == 'enclosure':
        return part.get('title'), [f'{item["name"]}: {item["text"]}' for item in part['inside']['parts'] + part['outside']['rows']]
    if kind == 'blocks':
        return part.get('title'), [f'{item["title"]}: {item["text"]}' for item in part['items']]
    if kind == 'fit':
        return part.get('title'), [f'{side["title"]}: {" ".join(side["items"])}' for side in (part['use'], part['other'])]
    return None, []


def tool_llms(config, root):
    """The tool's page as a text file an agent reads first."""
    repo = repo_url(config['key'])
    promise = config['promise']
    rows = [f'# {config["name"]}', '', f'> {promise if isinstance(promise, str) else " ".join(promise)}', '', ' '.join(config['description'].split()), '',
            LLMS['tool']['start'], '', '> ' + (config.get('prompt') or SHARED['tool_prompt'].format(url=repo))]
    asks = config.get('requests')
    if asks:
        rows += ['', f'## {asks.get("title", TOOL_SITE["requests"])}', '', *(f'- {item}' for item in asks['items'])]
    sections = tool_sections(config)
    for part in sections + [part['steps'] | {'kind': 'steps'} for part in sections if part.get('kind') == 'cards' and part.get('steps')]:
        title, points = section_points(part)
        if points:
            rows += ['', f'## {title or config["name"]}', '', *(f'- {point}' for point in points)]
    files = [(LLMS['overview']['source'], repo)]
    pointer = root/'smart-tool.json'
    if pointer.is_file():
        files.append((LLMS['tool']['manifest'], f'{repo}/blob/main/{json.loads(pointer.read_text(encoding="utf-8"))["manifest"]}'))
    linked = config.get('links', []) + [item for part in sections for item in (part.get('links') or []) + ((part.get('steps') or {}).get('links') or [])]
    files += [(item['label'], item.get('url') or f'{repo}/blob/main/{item["path"]}') for item in linked]
    rows += ['', f'## {LLMS["tool"]["links"]}', '', *(f'- [{label}]({url})' for label, url in dict(files).items())]
    return plain('\n'.join(rows)) + '\n'


def catalog_tools(root):
    """Read every listing into the values the catalog pages display."""
    categories, listings = load_catalog_metadata(root)
    tools = []
    for source in catalog_sources(root):
        slug = source.parent.name
        pointer = validate_pointer(read_json(source, root))
        repo = safe_url(pointer['repository']).removesuffix('.git')
        description = 'No manifest snapshot is available yet. Inspect the source repository for current guidance.'
        meta, prov = read_snapshot(source.parent, root)
        if meta:
            description = ' '.join(meta['description'].split())
        listing = listings.get(slug)
        category = listing['category'] if listing else ''
        category_label = None
        if categories is not None:
            category_label = categories[category]['label'] if category else STORE['other']
        platforms = meta.get('platforms', [])
        if not isinstance(platforms, list) or not all(isinstance(p, str) for p in platforms):
            raise ValueError(f'{slug}: invalid platforms')
        use_cases = meta.get('use_cases', [])
        if not isinstance(use_cases, list) or not all(isinstance(u, str) for u in use_cases):
            raise ValueError(f'{slug}: invalid use cases')
        source_url = repo
        if pointer['path'] != '.' or pointer['ref'] != 'main':
            source_url = f'{repo}/tree/{quote(pointer["ref"], safe="/")}'
            if pointer['path'] != '.':
                source_url += f'/{quote(pointer["path"], safe="/")}'
        segments = urlsplit(repo).path.strip('/').split('/')
        name = slug.replace('-', ' ').title()
        search_values = [str(meta.get('name', slug)), name, description, *use_cases, *platforms]
        if category_label:
            search_values.append(category_label)
        tools.append({
            'slug': slug, 'name': name, 'description': description,
            'tagline': first_sentence(description), 'use_cases': use_cases, 'platforms': platforms,
            'category': category, 'category_label': category_label,
            'state': recommendation_state(pointer, prov, listing, bool(meta)) if categories is not None else '',
            'source_url': source_url,
            'owner': segments[0] if len(segments) > 1 else urlsplit(repo).netloc, 'version': meta.get('version'),
            'search': esc(' '.join(search_values).lower()),
            'showcase': next((key for key in PAGES if key not in ('overview', 'catalog') and repo == repo_url(key)), None),
        })
    return categories, sorted(tools, key=lambda tool: (tool['name'].casefold(), tool['slug']))


def catalog(config, args, root):
    categories, tools = catalog_tools(root)

    def chip(identity, label, selected=False):
        return f'<button class="chip" type="button" data-category="{esc(identity)}" aria-pressed="{str(selected).lower()}">{html.escape(label, quote=True)}</button>'

    chips = ''
    if categories is not None:
        populated = {tool['category']: tool['category_label'] for tool in tools if tool['category']}
        chips = chip('', STORE['all'], True) + ''.join(
            chip(identity, label) for identity, label in sorted(populated.items(), key=lambda item: item[1].casefold()))
        if any(not tool['category'] for tool in tools):
            chips += chip(UNCLASSIFIED, STORE['other'])
        chips = f'<div class="chips" id="category-chips" role="group" aria-label="Category" hidden>{chips}</div>'
    recommended = [tool for tool in tools if tool['state'] == 'recommended']
    cards = ''.join(tool_card(tool['name'], tool['slug'], tool['tagline'], f'./tools/{tool["slug"]}/') for tool in recommended)
    featured = f'<section class="store-section" id="featured" aria-labelledby="featured-heading"><div class="store-section-head plain"><h2 id="featured-heading">{words(STORE["recommended"])}</h2></div><div class="card-grid three">{cards}</div></section>' if cards else ''
    count = f'{len(tools)} tool' if len(tools) == 1 else f'{len(tools)} tools'
    rows = ''.join(app_row(tool, './tools/') for tool in tools)
    empty, agent = STORE['empty'], STORE['agent']
    return f'''<section class="catalog-hero"><h1>{words(STORE['title'])}</h1>
    <p class="standfirst">{words(STORE['standfirst'])}</p>
    <div class="store-search" id="store-search" hidden>{icon('search', 20)}<input id="tool-search" type="search" placeholder="{esc(STORE['search'])}" aria-label="{esc(STORE['search'])}" autocomplete="off"></div></section>
    {chips}{featured}
    <section class="store-section" aria-labelledby="all-tools-heading"><div class="store-section-head"><h2 id="all-tools-heading">{words(STORE['all_tools'])}</h2><span class="visually-hidden" id="result-count" role="status">{count}</span></div><div class="app-list">{rows}</div></section>
    <div class="empty crop" id="empty-results" hidden><h3>{words(empty['title'])}</h3><p>{words(empty['text'])}</p><button class="button" type="button" data-clear>{words(empty['clear'])}</button></div>
    <section class="section split"><div><h2>{words(agent['title'])}</h2><p>{words(agent['text'])}</p></div>{get_started(FAMILY_PROMPT)}</section>'''


def listing_page(tool, tools, args):
    """A tool's own page in the catalog."""
    status = {'recommended': SHARED['recommended'], 'needs-review': SHARED['needs_review']}.get(tool['state'])
    pill = f'<p class="status-pill">{words(status)}</p>' if status else ''
    actions = button(SHARED['get'], '#get-started', 'action', 'down-right') + button(SHARED['github'], tool['source_url'], arrow='up-right')
    if tool['showcase']:
        actions += text_link(TOOL['website'], page_url(tool['showcase'], args))
    facts = []
    if tool['category_label']:
        facts.append(('Category', html.escape(tool['category_label'], quote=True)))
    facts.append(('Platform', esc(', '.join(PLATFORM_LABELS.get(p, p) for p in tool['platforms']) or TOOL['no_platform'])))
    facts.append(('Publisher', esc('Microsoft' if tool['owner'] == 'microsoft' else tool['owner'])))
    if tool['version']:
        facts.append(('Version', esc('v' + str(tool['version']).removeprefix('v'))))
    rest = tool['description'][len(tool['tagline']):].strip()
    about = ''
    if tool['use_cases']:
        cases = ''.join(f'<li>{esc(case)}</li>' for case in tool['use_cases'][:6])
        about = f'<section class="section split"><div><h2>{words(TOOL["use_cases"])}</h2>{f"<p>{esc(rest)}</p>" if rest else ""}</div><ul class="use-cases">{cases}</ul></section>'
    elif rest:
        about = f'<section class="section split"><div><h2>{words(TOOL["about"])}</h2></div><p>{esc(rest)}</p></section>'
    others = [other for other in tools if other is not tool]
    more = [other for other in others if tool['category'] and other['category'] == tool['category']]
    more += [other for other in others if other['state'] == 'recommended' and other not in more]
    more_section = f'<section class="section">{section_head(words(SHARED["more_tools"]))}<div class="app-list">{"".join(app_row(other, "../") for other in more[:3])}</div></section>' if more else ''
    return f'''<section class="store-head">{app_icon(tool['slug'], tool['name'])}<div>{pill}<h1>{esc(tool['name'])}</h1>{facts_row(facts)}<p class="standfirst">{esc(tool['tagline'])}</p><div class="actions">{actions}</div></div></section>
    <section class="section split" id="get-started"><div><h2>{words(SHARED['get_started']['title'])}</h2><p>{words(SHARED['get_started']['text'])}</p></div>{get_started(SHARED['tool_prompt'].format(url=tool['source_url']))}</section>
    {about}
    {more_section}'''


def listing_markdown(tool):
    """A tool's catalog page as Markdown."""
    version = 'v' + str(tool['version']).removeprefix('v') if tool['version'] else ''
    facts = [('Category', tool['category_label']), ('Platforms', plain(', '.join(PLATFORM_LABELS.get(p, p) for p in tool['platforms']))),
             ('Publisher', 'Microsoft' if tool['owner'] == 'microsoft' else plain(tool['owner'])), ('Version', plain(version)),
             ('Source', tool['source_url'])]
    if tool['state'] == 'recommended':
        facts.insert(0, ('Status', SHARED['recommended']))
    lines = [f'# {plain(tool["name"])}', '', plain(tool['description']), '', *(f'- {term}: {value}' for term, value in facts if value), '',
             f'## {LLMS["tool"]["get_started"]}', '', LLMS['tool']['start'], '', '> ' + SHARED['tool_prompt'].format(url=tool['source_url'])]
    if tool['use_cases']:
        lines += ['', f'## {LLMS["tool"]["use_cases"]}', '', *(f'- {plain(case)}' for case in tool['use_cases'])]
    return '\n'.join(lines) + '\n'


def build_listings(root, out, args, config):
    """Write one page per catalog tool at tools/<slug>/, and its Markdown copy."""
    _, tools = catalog_tools(root)
    for tool in tools:
        target = out/'tools'/tool['slug']
        target.mkdir(parents=True, exist_ok=True)
        page_config = dict(config, title=f'{tool["name"]} | Amplifier Smart Tools catalog', description=tool['tagline'])
        (target/'index.html').write_text(document(page_config, listing_page(tool, tools, args), args, '../../assets/', './index.md'), encoding='utf-8')
        (target/'index.md').write_text(listing_markdown(tool), encoding='utf-8')


def catalog_llms(config, args, root):
    """The catalog's llms.txt: every tool, by category, with a link to its Markdown page."""
    categories, tools = catalog_tools(root)
    base = page_url('catalog', args)

    def section(title, members):
        return ['', f'## {title}', '', *(f'- [{plain(tool["name"])}]({base}tools/{tool["slug"]}/index.md): {plain(tool["tagline"])}' for tool in members)]

    text = LLMS['catalog']
    lines = [f'# {text["title"]}', '', f'> {plain(config["description"])}', '',
             f'{text["start"]} {FAMILY_PROMPT}']
    recommended = [tool for tool in tools if tool['state'] == 'recommended']
    if recommended:
        lines += section(text['recommended'], recommended)
    if categories is None:
        lines += section(text['all_tools'], tools)
    else:
        labels = sorted({tool['category_label'] for tool in tools}, key=lambda label: (label == STORE['other'], label.casefold()))
        for label in labels:
            lines += section(label, [tool for tool in tools if tool['category_label'] == label])
    return '\n'.join(lines) + '\n'


def spec_markdown(root, name):
    """A chapter's source with links that resolve from the published copy."""
    files = {f'{chapter}.md' for chapter, _ in CHAPTERS}

    def target(match):
        url = urlsplit(match.group(1))
        path = url.path.removeprefix('./')
        fragment = f'#{url.fragment}' if url.fragment else ''
        if url.scheme or url.netloc or not path:
            return match.group(0)
        if path in files:
            return f'](./{"index.md" if path == "README.md" else path}{fragment})'
        source = (root/'spec'/path).resolve()
        if source.exists() and source.is_relative_to(root):
            view = 'tree' if source.is_dir() else 'blob'
            return f']({repo_url("overview")}/{view}/main/{source.relative_to(root).as_posix()}{fragment})'
        return match.group(0)

    return plain(re.sub(r'\]\(([^)\s]+)\)', target, (root/'spec'/f'{name}.md').read_text(encoding='utf-8')))


def overview_llms(config, args, root):
    """The overview's llms.txt: what a smart tool is, and links to the Markdown copy of each page."""
    base, catalog_url = page_url('overview', args), page_url('catalog', args)
    text = LLMS['overview']
    lines = [f'# {SHARED["name"]}', '', f'> {config["description"]}', '', ABOUT, '',
             f'{text["start"]} {FAMILY_PROMPT}', '',
             *(f'- {title}: {text}' for title, text in BENEFITS), '',
             f'## {text["sections"]["specification"]}', '',
             *(f'- [{label}]({base}spec/{"index" if name == "README" else name}.md)' for name, label in CHAPTERS), '',
             f'## {text["sections"]["catalog"]}', '', f'- [{text["all_tools"]}]({catalog_url}llms.txt): {text["all_tools_note"]}',
             *(f'- [{item["name"]}]({catalog_url}tools/{item["slug"]}/index.md): {item["tagline"]}' for item in config.get('featured', []))]
    posts = blog_posts(root)
    if posts:
        lines += ['', f'## {text["sections"]["blog"]}', '', *(f'- [{post["title"]}]({base}blog/{post["slug"]}/index.md): {post["summary"]}' for post in posts)]
    lines += ['', f'## {text["sections"]["optional"]}', '', f'- [{text["source"]}]({repo_url("overview")})',
              f'- [{text["conformance"]}]({repo_url("overview")}/tree/main/conformance)']
    return plain('\n'.join(lines)) + '\n'


def build_reader(root, out, args, config):
    """Render the checked-out specification; do not maintain a second copy."""
    from markdown_it import MarkdownIt
    md = MarkdownIt('commonmark', {'html': False}).enable('table')
    chapters = CHAPTERS
    chapter_files = {name+'.md': 'index.html' if name == 'README' else name+'.html' for name, _ in chapters}
    target = out/'spec'
    target.mkdir(exist_ok=True)
    for index, (name, label) in enumerate(chapters):
        source = root/'spec'/f'{name}.md'
        tokens = md.parse(plain(source.read_text()))
        headings = []
        used = {}
        for i, token in enumerate(tokens):
            if token.type == 'heading_open':
                text = tokens[i+1].content
                anchor = re.sub(r'[^a-z0-9 -]', '', text.lower()).replace(' ', '-')
                n = used.get(anchor, 0)
                used[anchor] = n + 1
                if n:
                    anchor += '-'+str(n)
                token.attrSet('id', anchor)
                if token.tag == 'h1':
                    tokens[i+1].content = SHARED['name'] if name == 'README' else text
                    tokens[i+1].children = md.parseInline(tokens[i+1].content)[0].children
                if token.tag == 'h2':
                    headings.append((anchor,text))
            for child in token.children or []:
                if child.type != 'link_open':
                    continue
                href = child.attrGet('href')
                url = urlsplit(href)
                if url.scheme or url.netloc or not url.path:
                    continue
                filename = url.path.removeprefix('./')
                if filename in chapter_files:
                    child.attrSet('href', urlunsplit(('', '', './'+chapter_files[filename], url.query, url.fragment)))
                else:
                    from posixpath import normpath
                    source_path = normpath('spec/'+filename)
                    child.attrSet('href', repo_url('overview')+'/blob/main/'+source_path+('#'+url.fragment if url.fragment else ''))
        rendered = md.renderer.render(tokens, md.options, {})
        chapter_nav = ''.join('<a href="./'+chapter_files[n+'.md']+'"'+(' aria-current="page"' if n == name else '')+'>'+esc(l)+'</a>' for n,l in chapters)
        toc = ''.join(link('#'+anchor,text) for anchor,text in headings)
        page_nav = ''
        if index:
            prev_name, prev_label = chapters[index-1]
            page_nav += text_link(READER['previous']+prev_label, './'+chapter_files[prev_name+'.md'], '')
        if index+1 < len(chapters):
            next_name, next_label = chapters[index+1]
            page_nav += text_link(READER['next']+next_label, './'+chapter_files[next_name+'.md'], 'right')
        body = f'''<div class="reader-layout"><aside class="reader-sidebar"><p class="eyebrow">{words(READER['title'])}</p><nav aria-label="Specification chapters">{chapter_nav}</nav>{link(repo_url('overview')+'/blob/main/spec/'+name+'.md',READER['source'],'reader-source')}</aside><details class="reader-toc" open><summary>{words(READER['outline'])}</summary><nav aria-label="On this page">{toc}</nav></details><article class="reader-content">{rendered}<nav class="reader-pagination" aria-label="Chapter navigation">{page_nav}</nav></article></div>'''
        page_config = dict(config, kind='reader', title=label+' | Amplifier Smart Tools specification',description='Read the '+label.lower()+' chapter of the Amplifier Smart Tools specification.')
        copy = ('index' if name == 'README' else name) + '.md'
        (target/chapter_files[name+'.md']).write_text(document(page_config, body, args, '../assets/', './'+copy))
        (target/copy).write_text(spec_markdown(root, name), encoding='utf-8')


def image_size(path):
    """Pixel size of a PNG or GIF, read from the file header."""
    head = path.read_bytes()[:24]
    size = None
    if head[:8] == b'\x89PNG\r\n\x1a\n' and len(head) == 24:
        size = int.from_bytes(head[16:20], 'big'), int.from_bytes(head[20:24], 'big')
    elif head[:6] in (b'GIF87a', b'GIF89a') and len(head) >= 10:
        size = int.from_bytes(head[6:8], 'little'), int.from_bytes(head[8:10], 'little')
    return size if size and all(size) else None


def blog_posts(root):
    """Posts at site/blog/<slug>/post.md, newest first."""
    posts = []
    for source in sorted((root/'site/blog').glob('*/post.md')):
        slug = source.parent.name
        if not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', slug):
            raise ValueError(f'{slug}: post directory must be a lowercase slug')
        parts = source.read_text(encoding='utf-8').split('---\n', 2)
        if len(parts) != 3 or parts[0].strip():
            raise ValueError(f'{slug}: post must start with front matter')
        try:
            meta = yaml.safe_load(parts[1])
        except yaml.YAMLError as error:
            raise ValueError(f'{slug}: front matter is not valid YAML') from error
        if not isinstance(meta, dict) or not all(meta.get(key) for key in ('title', 'date', 'author', 'summary')):
            raise ValueError(f'{slug}: front matter needs title, date, author, and summary')
        # A datetime is a date subclass, and it cannot be ordered against a plain date.
        if type(meta['date']) is not date:
            raise ValueError(f'{slug}: date must be written as YYYY-MM-DD')
        posts.append({'slug': slug, 'directory': source.parent, 'body': parts[2],
                      'title': str(meta['title']), 'date': meta['date'], 'author': str(meta['author']),
                      'summary': str(meta['summary']), 'affiliation': str(meta.get('affiliation', ''))})
    return sorted(posts, key=lambda post: (post['date'], post['slug']), reverse=True)


def post_body(post, md, target):
    """Render a post. An image alone in a paragraph becomes a figure; an italic line after it is the caption."""
    tokens = md.parse(plain(post['body']))
    parts, start, i = [], 0, 0
    while i < len(tokens):
        kids = (tokens[i + 1].children or []) if tokens[i].type == 'paragraph_open' else []
        if len(kids) != 1 or kids[0].type != 'image':
            if any(kid.type == 'image' for kid in tokens[i].children or []):
                raise ValueError(f'{post["slug"]}: an image must stand alone in its paragraph')
            i += 1
            continue
        parts.append(md.renderer.render(tokens[start:i], md.options, {}))
        name = unquote(kids[0].attrGet('src'))
        source = post['directory']/name
        if Path(name).name != name or not source.is_file():
            raise ValueError(f'{post["slug"]}: an image must be a file beside the post')
        shutil.copy2(source, target/name)
        size = image_size(source)
        dimensions = f' width="{size[0]}" height="{size[1]}"' if size else ''
        i += 3
        caption = ''
        following = (tokens[i + 1].children or []) if i < len(tokens) and tokens[i].type == 'paragraph_open' else []
        marks = [kid.type for kid in following if kid.type in ('em_open', 'em_close')]
        if len(following) > 2 and marks == ['em_open', 'em_close'] and following[0].type == 'em_open' and following[-1].type == 'em_close':
            caption = f'<figcaption class="image-caption">{md.renderer.renderInline(following[1:-1], md.options, {})}</figcaption>'
            i += 3
        parts.append(f'<figure><div class="product-image"><a href="./{quote(name)}"><img src="./{quote(name)}" alt="{esc(kids[0].content)}"{dimensions} loading="lazy"></a></div>{caption}</figure>')
        start = i
    parts.append(md.renderer.render(tokens[start:], md.options, {}))
    return ''.join(parts)


def post_markdown(post, published, address):
    """A post as Markdown, with the facts of its header. Images get the post's address, so they load from a pasted copy."""
    def image(match):
        beside = (post['directory']/unquote(match[2])).is_file()
        return f'![{match[1]}]({address if beside else ""}{match[2]})'

    body = re.sub(r'!\[([^\]]*)\]\(([^)\s]+)\)', image, post['body'].strip())
    byline = [published, f'{BLOG["written_by"]} {post["author"]}', post['affiliation']]
    return plain('\n'.join([f'# {post["title"]}', '', *filter(None, byline), '', body])) + '\n'


def build_blog(root, out, args, config):
    """Render site/blog into a list of posts and one page per post."""
    posts = blog_posts(root)
    if not posts:
        return
    from markdown_it import MarkdownIt
    md = MarkdownIt('commonmark', {'html': False})
    rows = ''
    for post in posts:
        target = out/'blog'/post['slug']
        target.mkdir(parents=True, exist_ok=True)
        published = f'{post["date"]:%B} {post["date"].day}, {post["date"].year}'
        facts = [('Published', esc(published)), ('Author', esc(post['author']))]
        if post['affiliation']:
            facts.append(('Affiliation', esc(post['affiliation'])))
        minutes = max(1, round(len(re.findall(r'\w+', post['body'])) / 230))
        facts.append(('Reading time', f'{minutes} {words(BLOG["reading"])}'))
        body = f'''<article class="reader-content post"><header class="post-header"><p class="eyebrow"><a href="../">{words(BLOG['title'])}</a></p><h1>{esc(post['title'])}</h1><div class="post-meta copy-region">{facts_row(facts)}<a class="copy-markdown" href="./index.md" data-copy-markdown>{icon('copy', 14)}{icon('check', 14)}{words(BLOG['copy'])}</a><p class="visually-hidden" role="status" aria-live="polite"></p></div></header>{post_body(post, md, target)}<nav class="reader-pagination" aria-label="Blog">{text_link(BLOG['all'], '../', '')}</nav></article>'''
        page_config = dict(config, kind='blog', title=f'{post["title"]} | Amplifier Smart Tools', description=post['summary'])
        (target/'index.html').write_text(document(page_config, body, args, '../../assets/', './index.md'), encoding='utf-8')
        (target/'index.md').write_text(post_markdown(post, published, f'{page_url("overview", args)}blog/{post["slug"]}/'), encoding='utf-8')
        rows += f'<article class="post-row"><p class="post-date">{esc(published)}</p><div><h2><a href="./{post["slug"]}/">{esc(post["title"])}</a></h2><p>{esc(post["summary"])}</p></div></article>'
    index = f'<section class="catalog-hero"><h1>{words(BLOG["title"])}</h1></section><div class="post-list">{rows}</div>'
    page_config = dict(config, kind='blog', title=f'{BLOG["title"]} | {SHARED["name"]}', description=BLOG['description'])
    (out/'blog/index.html').write_text(document(page_config, index, args, '../assets/'), encoding='utf-8')


def document(config, body, args, asset_prefix='./assets/', markdown=''):
    """Wrap a page body in the shared shell. `markdown` is the URL of the page's Markdown copy."""
    key = config['key']
    machine = f'<link rel="alternate" type="text/markdown" href="{markdown}">' if markdown else ''
    machine += f'<link rel="describedby" type="text/plain" href="{page_url(key, args)}llms.txt">'
    overview_url = page_url('overview', args)
    labels = SHARED['navigation']
    destinations = [
        (page_url('catalog', args), words(labels['catalog']), key == 'catalog'),
        (overview_url + 'blog/', words(labels['blog']), config['kind'] == 'blog'),
        (SPEC, words(labels['specification']), config['kind'] == 'reader'),
        (repo_url(key), words(labels['github']), False),
    ]
    nav = ''.join(f'<a href="{url}"' + (' aria-current="page"' if current else '') + f'>{label}</a>' for url, label, current in destinations)
    footer_links = ''.join(f'<a href="{url}">{label}</a>' for url, label, _ in destinations)
    menu_rows = ''.join(f'<li><a href="{url}"><span>{label}</span>{icon("arrow-up-right", 36)}</a></li>' for url, label, _ in destinations)
    brand = f'<a class="brand" href="{overview_url}">{mark()}<span class="brand-name">{words(SHARED["name"])}</span></a>'
    before, after = SHARED['credit']
    credit = f'<p class="credit">{words(before)} <strong>made</strong> {words(after)}</p>'
    title = config['title']
    description = config['description']
    accent = config.get('accent','#486b61')
    if not re.fullmatch(r'#[0-9a-fA-F]{6}',accent):
        raise ValueError('Accent must be a hex color')
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(title)}</title><meta name="description" content="{esc(description)}"><meta name="theme-color" content="#f3f0e8"><meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(description)}"><meta property="og:type" content="website"><link rel="icon" href="{asset_prefix}favicon.svg" type="image/svg+xml">{machine}<link rel="stylesheet" href="{asset_prefix}style.css"><script src="{asset_prefix}site.js" defer></script></head>
<body style="--accent:{accent}"><a class="skip" href="#main">{words(SHARED['skip'])}</a>{GLYPH_DEFS}<header class="masthead"><div class="wrap">{brand}<nav class="nav" aria-label="Main navigation">{nav}</nav><button class="menu-toggle" type="button" data-menu-toggle aria-expanded="false" aria-controls="menu" aria-label="Open menu">{icon('menu', 24)}</button></div></header><main class="wrap" id="main">{body}</main><footer class="site-footer"><div class="wrap footer-grid">{brand}<nav class="footer-links" aria-label="Footer">{footer_links}</nav>{credit}</div></footer><div class="menu" id="menu" role="dialog" aria-modal="true" aria-label="Menu"><div class="wrap"><div class="menu-head">{brand}<button class="menu-close" type="button" data-menu-close aria-label="Close menu">{icon('close', 22)}</button></div>{button(SHARED['menu_action'], overview_url + '#get-started', 'primary pill', 'right')}<ul class="menu-rows">{menu_rows}</ul></div></div></body></html>'''


def site_config(root):
    """A site's own content: site.yaml, or site.json."""
    source = root/'site/site.yaml'
    if source.is_file():
        return yaml.safe_load(source.read_text(encoding='utf-8'))
    return json.loads((root/'site/site.json').read_text(encoding='utf-8'))


def main():
    global SPEC
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='_site')
    parser.add_argument('--local', action='store_true', help='Link sibling pages on the same local preview server')
    parser.add_argument('--family-owner', help='Owner of the overview and catalog Pages previews, e.g. robotdad')
    args = parser.parse_args()
    SPEC = page_url('overview', args) + 'spec/'
    root = THEME.parent.parent
    config = site_config(root)
    out = Path(args.output).resolve()
    if out == root or root.is_relative_to(out) or out in (root/'site', THEME):
        raise ValueError('Output must be a separate generated-site directory')
    out.mkdir(parents=True, exist_ok=True)
    (out/'assets').mkdir(exist_ok=True)
    for filename in ('style.css','site.js'):
        shutil.copy2(THEME/filename, out/'assets'/filename)
    for asset_dir in (THEME/'assets', root/'site/assets'):
        if asset_dir.is_dir():
            shutil.copytree(asset_dir, out/'assets', dirs_exist_ok=True)
    media_paths = [config[k] for k in ('image', 'video') if config.get(k)]
    media_paths += [part[k] for part in config.get('capability_demos', []) + config.get('sections', []) for k in ('image', 'video') if part.get(k)]
    for media_path in media_paths:
        source = (root/media_path).resolve()
        if not source.is_relative_to(root):
            raise ValueError('Media must belong to the repository')
        shutil.copy2(source,out/'assets'/source.name)
    for part in config.get('sections', []):
        if part['kind'] == 'embed':
            directory, files = embed_sources(root, part)
            shutil.copytree(directory, out/directory.name, dirs_exist_ok=True)
            for source in files:
                shutil.copy2(source, out/directory.name/source.name)
    kind = config['kind']
    body = overview(config,args,root) if kind == 'overview' else catalog(config,args,root) if kind == 'catalog' else tool_page(config,args,root)
    doc = document(config, body, args)
    if kind == 'overview':
        build_reader(root, out, args, config)
        build_blog(root, out, args, config)
        (out/'llms.txt').write_text(overview_llms(config, args, root), encoding='utf-8')
    if kind == 'catalog':
        build_listings(root, out, args, config)
        (out/'llms.txt').write_text(catalog_llms(config, args, root), encoding='utf-8')
    if kind == 'tool':
        (out/'llms.txt').write_text(tool_llms(config, root), encoding='utf-8')
    (out/'index.html').write_text(doc,encoding='utf-8')
    (out/'.nojekyll').touch()
    (out/'assets/favicon.svg').write_text(favicon(config['key']))
    print(f'Built {config["key"]}: {out}')


if __name__ == '__main__':
    main()
