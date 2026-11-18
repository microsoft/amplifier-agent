# Website

The Amplifier Agent website is one page. Its content is `site.yaml`, and its media is in `site/assets/`.
The version on the page comes from `packages/python/pyproject.toml`.

```
site.yaml    the content of the page
build.py     builds the page; holds the header, the footer, and the sections only this site has
agent.css    styles for those sections
agent.js     scripts for those sections
theme/       a copy of the Amplifier Smart Tools theme: tokens, fonts, components, and shared sections
```

The canonical theme is maintained in `microsoft/amplifier-smart-tools/site/theme`. This repository carries a
copy in `site/theme/`, so a build does not depend on a remote theme. Theme version: 0.3.0. Theme code is MIT
licensed. The copy is excluded from this repository's lint and type checks.

## Project images

Three pictures in the Built with Amplifier Agent section come from the repositories of the projects they show.
Each is cropped to 16:10 and reduced. Each repository is MIT licensed.

```text
built-with-muxplex.webp                 bkrabach/muxplex, assets/branding/og/og-dark.png
built-with-reality-check.webp           microsoft/amplifier-smart-tool-reality-check, site/assets/reality-check-demo.png
built-with-digital-twin-universe.webp   microsoft/amplifier-smart-tool-digital-twin-universe, site/assets/dashboard.png
```

## Build and preview

From the repository root:

```sh
uv run site/build.py
python3 -m http.server 8000 --directory _site --bind 127.0.0.1
```

Open http://127.0.0.1:8000. The output goes to `_site/`, which Git ignores. This server does not answer range
requests, so a video cannot skip ahead. To try the video controls, use a server that does, for example
`npx http-server _site -p 8000 -a 127.0.0.1`.

## Publish

The Website workflow builds each pull request that changes the site and does not deploy it.
A push to main that changes the site or the release version builds the site and publishes it to
GitHub Pages. In **Settings > Pages**, select **GitHub Actions** as the source.

## Update the theme

From a checkout of `microsoft/amplifier-smart-tools`, run `site/sync_theme.py` with the path
of this repository. Review the diff and build again. It updates only `site/theme/`.
