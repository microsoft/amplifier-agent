# /// script
# requires-python = ">=3.12"
# dependencies = ["PyYAML==6.0.3"]
# ///
"""Build the Amplifier Agent website from site/site.yaml into _site/."""

import argparse
import html
import math
from pathlib import Path
import re
import shutil
import sys
import tomllib

import yaml

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
THEME = SITE / "theme"
# The theme directory goes first, so `build` is the theme's builder and not this file.
sys.path.insert(0, str(THEME))

# Both modules are in the theme directory. Import sorting groups them by what else is in the checkout, so it is off.
# isort: off
from graphics import GLYPH_DEFS, icon
import build as theme
# isort: on

# Family pages link to their published addresses.
FAMILY = argparse.Namespace(local=False, family_owner=None)


def logo(class_name: str, size: int) -> str:
    """The Amplifier logo."""
    return f'<img class="{class_name}" src="./assets/amplifier-logo.png" width="{size}" height="{size}" alt="">'


def release_version() -> str:
    """The version of the Python package, so the page follows each release."""
    project = tomllib.loads((ROOT / "packages/python/pyproject.toml").read_text(encoding="utf-8"))
    return project["project"]["version"]


def address(item: dict, repo: str) -> str:
    """Where a link goes. A path is a file in the repository."""
    return item.get("url") or f"{repo}/blob/main/{item['path']}"


def sample(text: str, title: str, identity: str) -> str:
    """Code on the dark surface, with a copy button."""
    return (
        '<div class="copy-region sample code"><div class="code-box"><div class="code-head">'
        f'<span>{theme.esc(title)}</span><button class="copy" type="button" data-copy-sample="{identity}" '
        f'aria-label="{theme.esc(theme.SHARED["copy"])} {theme.esc(title)}">{icon("copy", 14)}'
        f"{theme.words(theme.SHARED['copy'])}</button></div>"
        f'<pre id="{identity}" tabindex="0">{html.escape(text.strip(), quote=False)}</pre></div>'
        '<p class="status-message" role="status" aria-live="polite"></p></div>'
    )


def tabbed(identity: str, title: str, tabs: list[tuple[str, str]]) -> str:
    """Panels with one tab each. Without the script every panel shows under its own heading."""
    buttons = panels = ""
    for number, (label, panel) in enumerate(tabs, 1):
        tab, shown = f"{identity}-tab-{number}", f"{identity}-panel-{number}"
        buttons += (
            f'<button type="button" role="tab" id="{tab}" aria-controls="{shown}" '
            f'aria-selected="{"true" if number == 1 else "false"}">{theme.esc(label)}</button>'
        )
        panels += (
            f'<div class="tab-panel" role="tabpanel" id="{shown}" aria-labelledby="{tab}">'
            f'<h3 class="tab-title">{theme.esc(label)}</h3>{panel}</div>'
        )
    return (
        f'<div class="tabs" data-tabs><div class="tab-list" role="tablist" '
        f'aria-label="{theme.esc(title)}">{buttons}</div>{panels}</div>'
    )


def pane(block: dict, body: str) -> str:
    """One part of a drawn window: its step number and title, then its body."""
    step = f'<span class="beat">{int(block["step"]):02d}</span>' if block.get("step") else ""
    aside = f"<span>{theme.esc(block['aside'])}</span>" if block.get("aside") else ""
    return (
        f'<div class="pane {block["kind"]}"><p class="pane-title">{step}<strong>{theme.esc(block["title"])}</strong>'
        f"{aside}</p>{body}</div>"
    )


def run_pane(block: dict, legend: dict) -> str:
    """One task over time: who works on it, when, and on which model."""
    key = "".join(f'<li class="{tone}">{theme.esc(label)}</li>' for tone, label in legend.items())
    facts = "".join(f"<li>{theme.esc(fact)}</li>" for fact in block.get("facts", []))
    lanes = ""
    for lane in block["lanes"]:
        spans = "".join(
            f'<span class="trace-span {tone}" style="--start:{float(start)};--end:{float(end)}">'
            f'<span class="visually-hidden">{theme.esc(legend[tone])}</span></span>'
            for tone, start, end in ((span["tone"], span["start"], span["end"]) for span in lane["spans"])
        )
        spans += "".join(
            f'<span class="trace-mark" style="--at:{float(mark["at"])}">{theme.esc(mark["label"])}</span>'
            for mark in lane.get("marks", [])
        )
        lanes += (
            f'<li class="trace-lane"><div class="trace-who"><strong>{theme.esc(lane["name"])}</strong>'
            f'<span>{theme.esc(lane["text"])}</span></div><div class="trace-track">{spans}</div></li>'
        )
    return pane(
        block,
        f'<div class="trace-head"><ul class="tally">{facts}</ul><ul class="trace-legend">{key}</ul></div>'
        f'<ol class="trace-lanes">{lanes}</ol>',
    )


def window_bar(name: str, place: str) -> str:
    """The title bar of a drawn window: its name and where it is."""
    place = f'<span class="window-place">{theme.esc(place)}</span>'
    return f'<div class="window-bar"><strong>{theme.esc(name)}</strong>{place}</div>'


def videos_section(part: dict, repo: str, name: str) -> str:
    """Videos, one at a time, with a tab for each under them. A tab fills as its video plays. Every video has the
    browser's controls. Without the script every video shows under its title."""
    identity, videos = part["id"], part["videos"]
    tabs = shown = ""
    for number, video in enumerate(videos, 1):
        tab, panel, about = f"{identity}-tab-{number}", f"{identity}-video-{number}", f"{identity}-about-{number}"
        title = theme.esc(video["title"])
        source, poster, captions = (
            html.escape(f"./assets/{video[key]}", quote=True) for key in ("file", "poster", "captions")
        )
        tabs += (
            f'<button class="reel-tab" type="button" role="tab" id="{tab}" aria-controls="{panel}" '
            f'aria-selected="{"true" if number == 1 else "false"}"><span class="reel-progress" aria-hidden="true">'
            f"<span></span></span><span>{title}</span></button>"
        )
        shown += (
            f'<figure class="reel-video" role="tabpanel" id="{panel}" aria-labelledby="{tab}">'
            f'<figcaption><h3 class="reel-title">{title}</h3><p class="visually-hidden" id="{about}">'
            f'{theme.esc(video["alt"])}</p></figcaption><video src="{source}" poster="{poster}" controls muted '
            f'playsinline preload="{"metadata" if number == 1 else "none"}" aria-describedby="{about}">'
            f'<track kind="captions" src="{captions}" srclang="en" label="English"></video></figure>'
        )
    return (
        f'<section class="section videos"{theme.anchor(part)}><div class="reel" data-reel role="region" '
        f'aria-roledescription="carousel" aria-label="{theme.esc(part["label"])}">{shown}'
        f'<div class="reel-tabs" role="tablist" aria-label="Videos" hidden>{tabs}</div></div></section>'
    )


def included_links(part: dict) -> list[dict]:
    """The links of the included section, each with a label."""
    integrate = part["integrate"]
    linked = [item | {"label": item["name"]} for item in [*integrate["rows"], integrate["license"]]]
    for slide in part["diagram"]["slides"]:
        parts = [item for item in slide["parts"] if item.get("path") or item.get("url")]
        linked += [item | {"label": f"{slide['title']}: {item['name']}"} for item in parts]
        linked += [slide["link"]] if slide.get("link") else []
    return linked


def included_points(part: dict) -> tuple[str | None, list[str]]:
    """The included section's heading and points as plain text."""
    integrate = part["integrate"]
    lines = [f"{integrate['title']}: {item['name']}. {item['text']}" for item in integrate["rows"]]
    lines.append(integrate["license"]["name"])
    for slide in part["diagram"]["slides"]:
        parts = ", ".join(item["name"] for item in slide["parts"])
        lines.append(f"{slide['title']}: {slide['text']} {parts}.")
    return part["title"], lines


# Drawn controls of the slides: back and forward come from the theme's icons.
PAUSE, PLAY = (
    f'<svg class="icon {name}" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="{path}"/></svg>'
    for name, path in (
        ("pause", "M8 5v14M16 5v14"),
        ("play", "M7 4.5l12 7.5-12 7.5z"),
    )
)


def spoke_slide(slide: dict, number: int, count: int, middle: str, repo: str) -> str:
    """One slide: Amplifier Agent, and a line from it to each row of parts. The parts stand in at most four rows, so
    four parts are one column and nine are three rows of three. The parts of a row are joined."""
    total = len(slide["parts"])
    columns = math.ceil(total / 4)
    rows = math.ceil(total / columns)
    parts = ""
    for index, item in enumerate(slide["parts"]):
        name = theme.esc(item["name"])
        if item.get("path") or item.get("url"):
            name = f'<a href="{html.escape(address(item, repo), quote=True)}">{name}</a>'
        joined = ' class="joined"' if index % columns else ""
        parts += f'<li{joined} style="--i:{index}">{name}</li>'
    link = slide.get("link")
    link = f'<div class="actions">{theme.text_link(link["label"], address(link, repo))}</div>' if link else ""
    return (
        f'<div class="spoke-slide" role="group" aria-roledescription="slide" '
        f'aria-label="{number} of {count}: {theme.esc(slide["title"])}"><div class="spoke-stage">'
        f'<div class="spoke-middle" aria-hidden="true">{logo("spoke-logo", 36)}<span>{theme.esc(middle)}</span></div>'
        f'<ul class="spoke-parts" style="--columns:{columns};--rows:{rows}">{parts}</ul></div>'
        f'<div class="spoke-caption"><h4>{theme.esc(slide["title"])}</h4><p>{theme.esc(slide["text"])}</p>{link}</div>'
        "</div>"
    )


def spokes(diagram: dict, identity: str, repo: str) -> str:
    """The slides of the drawing, and the buttons that change them. Without the script every slide shows."""
    slides = diagram["slides"]
    shown = "".join(
        spoke_slide(slide, number, len(slides), diagram["middle"], repo) for number, slide in enumerate(slides, 1)
    )
    marks = "".join(
        f'<button class="spoke-mark" type="button" aria-label="Show slide {number}: {theme.esc(slide["title"])}">'
        "<span></span></button>"
        for number, slide in enumerate(slides, 1)
    )
    steps = [
        f'<button class="spoke-step{way}" type="button" aria-controls="{identity}-slides" aria-label="{label}">'
        f"{icon('arrow-right', 18)}</button>"
        for way, label in ((" back", "Previous slide"), ("", "Next slide"))
    ]
    return (
        f'<div class="spokes" data-spokes role="region" aria-roledescription="carousel" '
        f'aria-label="{theme.esc(diagram["label"])}">'
        f'<div class="spoke-slides" id="{identity}-slides">{shown}</div>'
        f'<div class="spoke-controls" hidden><button class="spoke-step" type="button" data-spoke-play '
        f'aria-controls="{identity}-slides" aria-label="Pause slides">{PAUSE}{PLAY}</button>{steps[0]}'
        f'<div class="spoke-marks">{marks}</div>{steps[1]}</div></div>'
    )


def included_section(part: dict, repo: str, name: str) -> str:
    """The parts a reader expects: links that make the integration easy, beside a drawing of what connects to the
    agent, one slide each."""
    integrate = part["integrate"]
    rows = "".join(
        f'<li><a href="{html.escape(address(item, repo), quote=True)}">{theme.esc(item["name"])}'
        f"{icon('arrow-up-right', 16)}</a><p>{theme.esc(item['text'])}</p></li>"
        for item in integrate["rows"]
    )
    license = integrate["license"]
    license = f'<a href="{html.escape(address(license, repo), quote=True)}">{theme.esc(license["name"])}</a>'
    return (
        f'<section class="section included"{theme.anchor(part)}>{theme.heading(part)}<div class="included-grid">'
        f'<div class="integrate"><h3>{theme.esc(integrate["title"])}</h3><ul class="integrate-rows">{rows}</ul>'
        f'<p class="integrate-license">{license}</p></div>{spokes(part["diagram"], part["id"], repo)}</div></section>'
    )


MOTIONS = {"follow", "tally", "rewind"}


def recall_cited(question: dict) -> dict[str, int]:
    """The number of each session that a point names. A point names one session, or a list of them."""
    cited = {}
    for number, point in enumerate(question["points"], 1):
        named = point["session"] if isinstance(point["session"], list) else [point["session"]]
        cited |= dict.fromkeys(named, number)
    return cited


def recall_field(store: dict, question: dict, part: dict) -> str:
    """Sessions in rows over time. The ones an answer reads are marked, and the ones a point names carry its number.
    A follow marks the row of the sessions it reads, and joins them with a line."""
    cited, reads, window = recall_cited(question), question.get("reads", []), question.get("window")
    rows, middles, picked, total, read = "", [], None, 0, 0
    for index, row in enumerate(store["rows"]):
        marks = ""
        for session in row["sessions"]:
            named = isinstance(session, dict)
            at, span = (session["at"], session["span"]) if named else session
            identity = session.get("id") if named else None
            middle = at + span / 2
            place = f"--at:{float(at)};--span:{float(span)}"
            inside = window is not None and window["start"] <= middle <= window["end"]
            if not (identity in reads or inside):
                marks += f'<span class="recall-session" style="{place}"></span>'
                continue
            read += 1
            if identity in reads:
                middles.append((middle, (index + 0.5) * 100 / len(store["rows"])))
                picked = index
            number = (
                f'<b>{cited[identity]}</b><span class="visually-hidden">{theme.esc(identity)}, '
                f"{theme.esc(part['read'])}</span>"
                if identity in cited
                else ""
            )
            order = f";--n:{cited[identity]}" if identity in cited else ""
            marks += f'<span class="recall-session read" style="{place};--middle:{middle:g}{order}">{number}</span>'
        total += len(row["sessions"])
        rows += (
            f"<li{' class=picked' if question['motion'] == 'follow' and index == picked else ''}>"
            f'<div class="recall-who"><strong>{theme.esc(row["name"])}</strong>'
            f'<span>{len(row["sessions"])}</span></div><div class="recall-track">{marks}</div></li>'
        )
    over = ""
    if question["motion"] == "follow":
        if not middles or len({y for _, y in middles}) != 1:
            raise ValueError(f"A follow reads sessions with ids, all in one row: {question['text']}")
        line = " ".join(f"{x:g},{y:g}" for x, y in sorted(middles))
        over = (
            f'<div class="recall-over" style="--row:{picked};--rows:{len(store["rows"])}" aria-hidden="true">'
            f'<span class="recall-head"></span><svg viewBox="0 0 100 100" preserveAspectRatio="none">'
            f'<polyline points="{line}"/></svg></div>'
        )
    elif window:
        head = '<span class="recall-head"></span>' if question["motion"] == "rewind" else ""
        over = (
            f'<div class="recall-over" aria-hidden="true"><span class="recall-band" style="--start:{window["start"]};'
            f'--end:{window["end"]}">{theme.esc(window["label"])}</span>{head}</div>'
        )
    tally = f"<li>{len(store['rows'])} {theme.esc(store['rows_label'])}</li><li>{total} {theme.esc(part['unit'])}</li>"
    tally += f'<li class="read">{read} {theme.esc(part["read"])}</li>'
    axis = "".join(f"<li>{theme.esc(label)}</li>" for label in store.get("axis", part["axis"]))
    start = f' style="--from:{window["start"]}"' if window else ""
    return (
        f'<div class="pane recall-map"><div class="trace-head"><p class="pane-title">'
        f'<strong>{theme.esc(store["title"])}</strong></p><ul class="tally">{tally}</ul></div>'
        f'<div class="recall-field"{start}><ol class="recall-rows">{rows}</ol>{over}</div>'
        f'<ul class="recall-axis" aria-hidden="true">{axis}</ul></div>'
    )


def recall_answer(question: dict, part: dict) -> str:
    """What comes back: one sentence, then the points. Each point has the number of the sessions it is from."""
    points = "".join(
        f'<li style="--i:{number}"><b>{number}</b><p>{theme.esc(point["text"])}</p></li>'
        for number, point in enumerate(question["points"], 1)
    )
    return (
        f'<div class="pane recall-answer"><p class="pane-title"><strong>{theme.esc(part["answer"])}</strong></p>'
        f'<p class="recall-lead" style="--i:0">{theme.esc(question["answer"])}</p>'
        f'<ul class="recall-points">{points}</ul></div>'
    )


def recall_section(part: dict, repo: str, name: str) -> str:
    """Questions about recorded sessions, one tab each: the sessions an answer reads, and the answer. Each question
    has its own motion."""
    buttons = panels = ""
    for number, question in enumerate(part["questions"], 1):
        if question["motion"] not in MOTIONS:
            raise ValueError(f"motion in site.yaml must be one of {sorted(MOTIONS)}: {question['text']}")
        tab, shown = f"{part['id']}-tab-{number}", f"{part['id']}-panel-{number}"
        buttons += (
            f'<button type="button" role="tab" id="{tab}" aria-controls="{shown}" '
            f'aria-selected="{"true" if number == 1 else "false"}"><span>{theme.esc(question["label"])}</span>'
            f"{theme.esc(question['text'])}</button>"
        )
        panels += (
            f'<div class="tab-panel" role="tabpanel" id="{shown}" aria-labelledby="{tab}">'
            f'<h3 class="tab-title">{theme.esc(question["text"])}</h3><div class="window recall-figure" data-recall '
            f'data-motion="{question["motion"]}">'
            f'<div class="recall-body">{recall_field(part["stores"][question["store"]], question, part)}'
            f"{recall_answer(question, part)}</div></div></div>"
        )
    link = theme.text_link(part["link"]["label"], address(part["link"], repo))
    return (
        f'<section class="section recall"{theme.anchor(part)}>{theme.heading(part)}'
        f'<div class="tabs recall-tabs" data-tabs><div class="tab-list" role="tablist" aria-orientation="vertical" '
        f'aria-label="{theme.esc(part["ask"])}">{buttons}</div>{panels}</div>'
        f'<div class="actions recall-link">{link}</div></section>'
    )


def tool_flow(text: dict) -> str:
    """Who calls a Smart Tool, what the tool holds, and what comes back. It is the theme's flow_diagram, with an
    icon from each row of the left box."""

    def item(name: str, swatch: str, row: dict) -> str:
        return (
            f'<div class="flow-item"><span class="tile" style="--swatch:var(--{swatch})">{icon(name, 22)}</span>'
            f"<div><strong>{theme.esc(row['name'])}</strong><span>{theme.esc(row['text'])}</span></div></div>"
        )

    def arrow(label: str) -> str:
        return f'<div class="flow-arrow"><span>{theme.esc(label)}</span>{icon("arrow-right", 28)}</div>'

    host, tool, caller = text["host"], text["tool"], text["caller"]
    hosts = "".join(item(row["icon"], "pastel-butter", row) for row in host["rows"])
    shapes = (("loop", "blue"), ("spark", "red"), ("plug", "green"), ("library", "yellow"))
    parts = "".join(
        f'<div><span class="tile" style="--swatch:var(--tint-{tint})">{icon(name, 22)}</span>{theme.esc(title)}</div>'
        for (name, tint), title in zip(shapes, tool["parts"], strict=True)
    )
    results = "".join(
        item(name, "pastel-mint", row) for name, row in zip(("check", "box"), caller["rows"], strict=True)
    )
    return (
        f'<div class="flow"><div class="flow-panel"><h3>{theme.esc(host["title"])}</h3>{hosts}</div>'
        f'{arrow(text["intent"])}<div class="flow-panel tool"><h3>{theme.esc(tool["title"])}</h3>'
        f'{item("terminal", "pastel-stone", tool["row"])}<div class="flow-core"><p class="eyebrow">'
        f'{theme.esc(tool["core"])}</p><div class="flow-quad">{parts}</div></div></div>{arrow(text["result"])}'
        f'<div class="flow-panel"><h3>{theme.esc(caller["title"])}</h3>{results}</div></div>'
        f'<p class="dimension">{theme.esc(text["footer"])}</p>'
    )


def smart_tools_section(part: dict, repo: str, name: str) -> str:
    """What a Smart Tool is, with a drawing of how it works."""
    links = "".join(theme.text_link(item["label"], address(item, repo)) for item in part.get("links", []))
    return (
        f'<section class="section smart-tools"{theme.anchor(part)}>{theme.heading(part)}'
        f'{tool_flow(part["diagram"])}<div class="actions">{links}</div></section>'
    )


def level_track(level: dict) -> str:
    """How full something is over the run, as a line under the limit where the agent acts."""
    line = " ".join(f"{float(at):g},{100 - float(full):g}" for at, full in level["points"])
    limit = float(level["limit"])
    return (
        '<div class="trace-track level"><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">'
        f'<polygon points="0,100 {line} 100,100"/><polyline points="{line}"/>'
        f'<line x1="0" y1="{100 - limit:g}" x2="100" y2="{100 - limit:g}"/></svg>'
        f'<span class="long-limit" style="--limit:{limit:g}" aria-hidden="true">{theme.esc(level["label"])}</span>'
        f'<span class="visually-hidden">{theme.esc(level["text"])}</span></div>'
    )


def span_track(lane: dict, legend: dict) -> str:
    """A lane's stretches of work. A stretch with a label is a turn, and the others take the color of their model."""
    spans = "".join(
        f'<span class="trace-span {span["tone"]}" style="--start:{float(span["start"])};--end:{float(span["end"])};'
        f'--row:{int(span.get("row", 0))}">'
        + (
            theme.esc(span["label"])
            if span.get("label")
            else f'<span class="visually-hidden">{theme.esc(legend[span["tone"]])}</span>'
        )
        + "</span>"
        for span in lane["spans"]
    )
    stack = f' stack" style="--rows:{int(lane["rows"])}' if lane.get("rows") else ""
    return f'<div class="trace-track{stack}">{spans}</div>'


def run_record(run: dict, moments: list[dict], identity: str) -> str:
    """One long task from the agent's side: lanes over time, and a pin for each moment that opens its note."""
    legend = run["legend"]
    key = "".join(f'<li class="{tone}">{theme.esc(label)}</li>' for tone, label in legend.items())
    aside = f"<span>{theme.esc(run['aside'])}</span>" if run.get("aside") else ""
    lanes = "".join(
        f'<li class="long-lane"><div class="trace-who"><strong>{theme.esc(lane["name"])}</strong>'
        f"{f'<span>{theme.esc(lane["text"])}</span>' if lane.get('text') else ''}</div>"
        f"{level_track(lane['level']) if lane.get('level') else span_track(lane, legend)}</li>"
        for lane in run["lanes"]
    )
    ticks = "".join(f'<li style="--at:{float(tick["at"])}">{theme.esc(tick["label"])}</li>' for tick in run["ticks"])
    pins = guides = notes = ""
    for number, moment in enumerate(moments, 1):
        note = f"{identity}-moment-{number}"
        for at in moment["at"] if isinstance(moment["at"], list) else [moment["at"]]:
            pins += (
                f'<li style="--at:{float(at)}"><a class="long-pin" href="#{note}" data-moment="{number}" '
                f'aria-label="{number:02d} {theme.esc(moment["title"])}">{number:02d}</a></li>'
            )
            guides += f'<span style="--at:{float(at)}" data-moment="{number}"></span>'
        notes += (
            f'<li id="{note}" data-moment="{number}"><p class="long-when"><span class="beat">{number:02d}</span>'
            f"<span>{theme.esc(moment['time'])}</span></p><h3>{theme.esc(moment['title'])}</h3>"
            f"<p>{theme.esc(moment['text'])}</p></li>"
        )
    steps = "".join(
        f'<button type="button" data-step="{step}" aria-label="{label} moment">{icon("arrow-right", 18)}</button>'
        for step, label in ((-1, "Previous"), (1, "Next"))
    )
    return (
        f'<div class="long-record" data-moments><div class="long-head"><p><strong>{theme.esc(run["title"])}</strong>'
        f'{aside}</p><ul class="trace-legend">{key}</ul></div>'
        f'<div class="long-chart"><div class="long-guides" aria-hidden="true">{guides}</div>'
        f'<ol class="long-pins">{pins}</ol><ol class="long-lanes">{lanes}</ol>'
        f'<ol class="long-ticks" aria-hidden="true">{ticks}</ol></div>'
        f'<div class="long-stage"><ol class="long-moments" aria-live="polite">{notes}</ol>'
        f'<div class="long-steps">{steps}</div></div></div>'
    )


def long_run_section(part: dict, repo: str, name: str) -> str:
    """One long task from the agent's side, then what an application sets for delegated work, one tab each."""
    set_by = part["delegation"]
    tabs = []
    for item in set_by["settings"]:
        panel = (
            f'<div class="long-setting"><p>{theme.esc(item["text"])}</p><div class="code-box"><div class="code-head">'
            f"<span>{theme.esc(item['file'])}</span></div>"
            f'<pre tabindex="0">{html.escape(item["code"].strip(), quote=False)}</pre></div></div>'
        )
        tabs.append((item["label"], panel))
    links = "".join(theme.text_link(item["label"], address(item, repo)) for item in part.get("links", []))
    links = f'<div class="actions">{links}</div>' if links else ""
    return (
        f'<section class="section long-run"{theme.anchor(part)}>{theme.heading(part)}'
        f"{run_record(part['run'], part['moments'], part['id'])}"
        f'<div class="split long-delegation"><div><h3>{theme.esc(set_by["title"])}</h3>'
        f"<p>{theme.esc(set_by['text'])}</p>{links}</div>"
        f"{tabbed(f'{part["id"]}-settings', set_by['title'], tabs)}</div></section>"
    )


def lead(part: dict, repo: str) -> str:
    """The short column beside a drawing: a heading, a text, and a link."""
    return (
        f'<div class="proof-lead"><h3>{theme.esc(part["title"])}</h3><p>{theme.esc(part["text"])}</p>'
        f'<div class="actions">{theme.text_link(part["link"]["label"], address(part["link"], repo))}</div></div>'
    )


def trial_part(part: dict, repo: str) -> str:
    """One evaluation task from its file to its grade: the task, who works in order, and the rubric."""
    task, run, grade = part["task"], part["run"], part["grade"]
    panes = pane(task | {"kind": "task", "step": 1}, sample(task["code"], task["excerpt"], "trial-task"))
    panes += run_pane(run | {"kind": "run", "step": 2}, run["legend"])
    criteria = "".join(f"<li>{theme.esc(item)}</li>" for item in grade["criteria"])
    panes += pane(grade | {"kind": "grade", "step": 3}, f'<ol class="rubric">{criteria}</ol>')
    return (
        f'<div class="window trial">{window_bar(part["name"], part["place"])}'
        f'<div class="trial-body">{panes}</div></div>'
    )


def flow_panel(side: dict, class_name: str = "") -> str:
    """One box of a flow: a title, a note, and rows. A row with own is the agent's own."""
    rows = ""
    for row in side["rows"]:
        tile = f'<span class="tile">{icon(row["icon"], 22)}</span>' if row.get("icon") else ""
        own = " own" if row.get("own") else ""
        rows += f'<div class="flow-item{own}">{tile}<strong>{theme.esc(row["name"])}</strong></div>'
    note = f'<p class="eyebrow">{theme.esc(side["note"])}</p>' if side.get("note") else ""
    return f'<div class="flow-panel {class_name}"><h3>{theme.esc(side["title"])}</h3>{note}{rows}</div>'


def matrix_part(part: dict, repo: str) -> str:
    """One task run by several agents: what each agent gets, who runs it, and what is recorded."""
    arrow = f'<div class="flow-arrow">{icon("arrow-right", 28)}</div>'
    return (
        f'<div class="flow matrix">{flow_panel(part["given"])}{arrow}{flow_panel(part["agents"], "alone")}'
        f"{arrow}{flow_panel(part['recorded'])}</div>"
    )


def path_part(part: dict, repo: str) -> str:
    """The order a feature goes through, one station for each source of truth."""
    stations = "".join(
        f'<li{" class=own" if item.get("own") else ""}><h4><a href="{html.escape(address(item, repo), quote=True)}">'
        f"{theme.esc(item['name'])}</a></h4><p>{theme.esc(item['text'])}</p></li>"
        for item in part["stations"]
    )
    return f'<ol class="path">{stations}</ol>'


PROOFS = {"trial": trial_part, "matrix": matrix_part, "path": path_part}


def evaluations_section(part: dict, repo: str, name: str) -> str:
    """How the agent is held to its contracts, one tab each: a short column beside a drawing."""
    tabs = [
        (piece["label"], f'<div class="proof">{lead(piece, repo)}{PROOFS[piece["kind"]](piece, repo)}</div>')
        for piece in part["parts"]
    ]
    return (
        f'<section class="section evaluations"{theme.anchor(part)}>{theme.heading(part)}'
        f"{tabbed(part['id'], part['title'], tabs)}</section>"
    )


def plate(project: dict) -> str:
    """The picture on a slide: the project's own image, its Smart Tool artwork, or a drawing of two boxes."""
    if project.get("image"):
        source = html.escape(f"./assets/{project['image']}", quote=True)
        return f'<div class="plate"><img src="{source}" alt="{theme.esc(project["alt"])}" loading="lazy"></div>'
    if project.get("tool"):
        slug = project["tool"]
        return (
            f'<div class="plate field{" art" if slug in theme.ARTWORK else ""}" style="--field:{theme.field(slug)}" '
            f'aria-hidden="true"><svg viewBox="0 0 64 64">{theme.tool_art(slug, project["name"])}</svg></div>'
        )
    drawing = project["drawing"]
    start, end = (
        f'<div class="plate-box{own}"><strong>{theme.esc(box["name"])}</strong>'
        f"<span>{theme.esc(box['text'])}</span></div>"
        for box, own in ((drawing["from"], ""), (drawing["to"], " own"))
    )
    link = f'<div class="plate-link">{icon("arrow-right", 20)}<span>{theme.esc(drawing["link"])}</span></div>'
    return f'<div class="plate drawing" aria-hidden="true">{start}{link}{end}</div>'


def projects_section(part: dict, repo: str, name: str) -> str:
    """Projects that use the agent, one slide each in a row that scrolls sideways, over a band of steps."""
    slides = ""
    for project in part["projects"]:
        url = theme.safe_url(project["url"])
        slides += (
            f'<li class="slide"><article class="project">{plate(project)}<div class="project-body">'
            f'<div class="project-head"><h3>{theme.esc(project["name"])}</h3>'
            f'<span class="beat">{theme.esc(project["face"])}</span></div><p>{theme.esc(project["text"])}</p>'
            f"{theme.text_link(url.removeprefix('https://github.com/'), url)}</div></article></li>"
        )
    track = f"{part['id']}-track"
    steps = "".join(
        f'<button class="carousel-step{way}" type="button" aria-controls="{track}" aria-label="{label}">'
        f"{icon('arrow-right', 20)}</button>"
        for way, label in ((" back", "Previous projects"), ("", "Next projects"))
    )
    under = theme.band(part["steps"], repo) if part.get("steps") else ""
    return (
        f'<section class="section projects"{theme.anchor(part)}>{theme.heading(part)}'
        f'<div class="carousel" data-carousel role="group" aria-roledescription="carousel" '
        f'aria-label="{theme.esc(part["title"])}"><ul class="carousel-track" id="{track}" role="list" tabindex="0" '
        f'aria-label="Projects">{slides}</ul><div class="carousel-controls" hidden>'
        f'<div class="carousel-marks" aria-hidden="true"></div><div class="carousel-steps">{steps}</div></div></div>'
        f"{under}</section>"
    )


SECTION_KINDS = theme.SECTION_KINDS | {
    "videos": videos_section,
    "recall": recall_section,
    "smart-tools": smart_tools_section,
    "long-run": long_run_section,
    "evaluations": evaluations_section,
    "included": included_section,
    "projects": projects_section,
}


def hero(config: dict, repo: str) -> str:
    name = config["name"]
    facts = [("Version", f"v{theme.esc(release_version())}")]
    if config.get("facts"):
        facts.append(("Use it from", theme.esc(config["facts"])))
    identity = (
        f'<div class="tool-id">{logo("agent-logo", 52)}'
        f'<div><p class="tool-name">{theme.esc(name)}</p>{theme.facts_row(facts)}</div></div>'
    )
    standfirst = f'<p class="standfirst">{theme.esc(config["standfirst"])}</p>' if config.get("standfirst") else ""
    links = "".join(theme.text_link(item["label"], address(item, repo)) for item in config.get("links", []))
    asks = config.get("requests")
    asked = theme.requests(asks["title"], asks["items"]) if asks else ""
    return (
        f'<section class="hero"><div class="hero-grid"><div>{identity}<h1>{theme.lines(config["promise"])}</h1>'
        f'{standfirst}<div class="actions">{theme.button(theme.SHARED["github"], repo, "action", "up-right")}{links}'
        f'</div></div><div class="hero-side"><div class="hero-start" id="get-started">'
        f'<p class="eyebrow">{theme.words(config["prompt_title"])}</p>'
        f"{theme.code_box(config['prompt'], theme.SHARED['prompt_label'], 'prompt')}</div>{asked}</div></div></section>"
    )


def document(config: dict, body: str) -> str:
    """The page: header, body, footer, and the phone menu."""
    repo, name, tint = config["repository"], config["name"], config["tint"]
    title, description = theme.esc(config["title"]), theme.esc(" ".join(config["description"].split()))
    places = [(html.escape(address(item, repo), quote=True), theme.esc(item["label"])) for item in config["navigation"]]
    nav = "".join(f'<a href="{url}">{label}</a>' for url, label in places)
    rows = "".join(
        f'<li><a href="{url}"><span>{label}</span>{icon("arrow-up-right", 36)}</a></li>' for url, label in places
    )
    # The header and the menu name the agent in words. The footer adds the logo.
    brand = f'<a class="brand" href="./"><span class="brand-name">{theme.esc(name)}</span></a>'
    marked = f'<a class="brand" href="./">{logo("brand-mark", 32)}<span class="brand-name">{theme.esc(name)}</span></a>'
    before, after = theme.SHARED["credit"]
    credit = f'<p class="credit">{theme.words(before)} <strong>made</strong> {theme.words(after)}</p>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><meta name="description" content="{description}"><meta name="theme-color" content="#f3f0e8">
<meta property="og:title" content="{title}"><meta property="og:description" content="{description}">
<meta property="og:type" content="website"><link rel="icon" href="./assets/favicon.png" type="image/png">
<link rel="apple-touch-icon" href="./assets/apple-touch-icon.png">
<link rel="describedby" type="text/plain" href="./llms.txt"><link rel="stylesheet" href="./assets/style.css">
<link rel="stylesheet" href="./assets/agent.css"><script src="./assets/site.js" defer></script>
<script src="./assets/agent.js" defer></script></head>
<body><a class="skip" href="#main">{theme.words(theme.SHARED["skip"])}</a>{GLYPH_DEFS}
<header class="masthead"><div class="wrap">{brand}<nav class="nav" aria-label="Main navigation">{nav}</nav>
<button class="menu-toggle" type="button" data-menu-toggle aria-expanded="false" aria-controls="menu"
aria-label="Open menu">{icon("menu", 24)}</button></div></header>
<main class="wrap" id="main"><div class="tool-site tinted agent-site" style="--tint:{tint}">{body}</div></main>
<footer class="site-footer"><div class="wrap footer-grid">{marked}
<nav class="footer-links" aria-label="Footer">{nav}</nav>{credit}</div></footer>
<div class="menu" id="menu" role="dialog" aria-modal="true" aria-label="Menu"><div class="wrap">
<div class="menu-head">{brand}<button class="menu-close" type="button" data-menu-close aria-label="Close menu">
{icon("close", 22)}</button></div>{theme.button(theme.SHARED["github"], repo, "primary pill", "up-right")}
<ul class="menu-rows">{rows}</ul></div></div></body></html>
"""


def points(part: dict) -> tuple[str | None, list[str]]:
    """A section's heading and points as plain text."""
    kind = part["kind"]
    if kind == "videos":
        return part["label"], [f"{video['title']}: {video['alt']}" for video in part["videos"]]
    if kind == "included":
        return included_points(part)
    if kind == "recall":
        asked = [
            f"{question['label']}: {question['text']} {part['answer']}: {question['answer']}"
            for question in part["questions"]
        ]
        return part["title"], [part["text"], *asked]
    if kind == "smart-tools":
        diagram = part["diagram"]
        lines = [" ".join(part["text"].split())]
        host, tool, caller = diagram["host"], diagram["tool"], diagram["caller"]
        lines.append(f"{host['title']}: {'; '.join(f'{row["name"]}, {row["text"]}' for row in host['rows'])}.")
        row = tool["row"]
        lines.append(f"{tool['title']}: {row['name']}, {row['text']}. {tool['core']}: {', '.join(tool['parts'])}.")
        lines.append(f"{caller['title']}: {'; '.join(f'{row["name"]}, {row["text"]}' for row in caller['rows'])}.")
        lines.append(diagram["footer"])
        return part["title"], lines
    if kind == "long-run":
        set_by = part["delegation"]
        lines = [part["text"], *(f"{moment['title']} {moment['text']}" for moment in part["moments"])]
        lines += [f"{set_by['title']} {set_by['text']}", *(item["text"] for item in set_by["settings"])]
        return part["title"], lines
    if kind == "evaluations":
        lines = [part["text"]]
        for piece in part["parts"]:
            lines.append(f"{piece['label']}: {piece['title']} {piece['text']}")
            lines += piece.get("grade", {}).get("criteria", [])
            sides = [piece[side] for side in ("given", "agents", "recorded") if piece.get(side)]
            lines += [f"{side['title']}: {', '.join(row['name'] for row in side['rows'])}" for side in sides]
            lines += [f"{item['name']}: {item['text']}" for item in piece.get("stations", [])]
        return part["title"], lines
    if kind == "projects":
        projects = [f"{item['name']} ({item['face']}): {item['text']} {item['url']}" for item in part["projects"]]
        return part["title"], projects
    return theme.section_points(part)


def llms(config: dict) -> str:
    """The page as a text file an agent reads first."""
    repo = config["repository"]
    rows = [f"# {config['name']}", "", f"> {' '.join(config['promise'])}", "", " ".join(config["description"].split())]
    rows += ["", "Paste this into your agent to start:", "", f"> {config['prompt']}"]
    asks = config.get("requests")
    if asks:
        rows += ["", f"## {asks['title']}", "", *(f"- {item}" for item in asks["items"])]
    sections = config["sections"]
    steps = [part["steps"] | {"kind": "steps"} for part in sections if part.get("steps")]
    linked = [*config.get("navigation", []), *config.get("links", [])]
    for part in sections + steps:
        heading, lines = points(part)
        if lines:
            rows += ["", f"## {heading or config['name']}", "", *(f"- {' '.join(line.split())}" for line in lines)]
        linked += [*part.get("faces", []), *part.get("links", [])]
        linked += included_links(part) if part["kind"] == "included" else []
        linked += [piece["link"] for piece in part.get("parts", [])]
        # An enclosure's link is the label on its arrow, not an address.
        linked += [part[item] for item in ("link", "doc") if isinstance(part.get(item), dict)]
    links = {item.get("label") or f"{item['name']} quickstart": address(item, repo) for item in linked}
    rows += ["", "## Links", "", *(f"- [{label}]({url})" for label, url in links.items())]
    return theme.plain("\n".join(rows)) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="_site")
    args = parser.parse_args()
    out = Path(args.output).resolve()
    if out == ROOT or ROOT.is_relative_to(out) or out.is_relative_to(SITE):
        raise ValueError("Output must be a directory outside site/ that holds only the generated website")
    config = yaml.safe_load((SITE / "site.yaml").read_text(encoding="utf-8"))
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", config["tint"]):
        raise ValueError("tint in site.yaml must be a hex color such as '#f7c9a3'")
    repo = theme.safe_url(config["repository"])
    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for source in (THEME / "style.css", THEME / "site.js", SITE / "agent.css", SITE / "agent.js"):
        shutil.copy2(source, assets / source.name)
    for directory in (THEME / "assets", SITE / "assets"):
        if directory.is_dir():
            shutil.copytree(directory, assets, dirs_exist_ok=True)
    for media in (part[kind] for part in config["sections"] for kind in ("image", "video") if part.get(kind)):
        source = (ROOT / media).resolve()
        if not source.is_relative_to(ROOT):
            raise ValueError(f"{media} in site.yaml must be a file in this repository")
        shutil.copy2(source, assets / source.name)
    body = hero(config, repo) + "".join(
        SECTION_KINDS[part["kind"]](part, repo, config["name"]) for part in config["sections"]
    )
    (out / "index.html").write_text(document(config, body), encoding="utf-8")
    (out / "llms.txt").write_text(llms(config), encoding="utf-8")
    (out / ".nojekyll").touch()
    print(f"Built {config['name']}: {out}")


if __name__ == "__main__":
    main()
