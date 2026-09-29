#!/usr/bin/env python3
"""Fail when a rendered MkDocs site contains broken internal links/assets."""

from __future__ import annotations

import argparse
import posixpath
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

EXTERNAL_SCHEMES = {"http", "https", "mailto", "tel", "data", "javascript"}


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.links.append(("href", str(values["href"])))
        elif tag in {"img", "script", "source"} and values.get("src"):
            self.links.append(("src", str(values["src"])))
        elif tag == "link" and values.get("href"):
            self.links.append(("href", str(values["href"])))


def _candidate(site: Path, page: Path, raw: str, site_prefix: str) -> Path | None:
    raw = raw.strip()
    if not raw or raw.startswith("#") or raw.startswith("//"):
        return None

    parsed = urlsplit(raw)
    if parsed.scheme.lower() in EXTERNAL_SCHEMES or parsed.netloc:
        return None

    path = unquote(parsed.path)
    if not path:
        return None

    prefix = "/" + site_prefix.strip("/") + "/" if site_prefix.strip("/") else "/"
    if path.startswith(prefix):
        path = "/" + path[len(prefix):]
    if path.startswith("/"):
        relative = posixpath.normpath(path.lstrip("/"))
    else:
        base = page.parent.relative_to(site).as_posix()
        relative = posixpath.normpath(posixpath.join(base, path))

    if relative == ".." or relative.startswith("../"):
        return site / "__outside_site__"

    target = site / relative
    if path.endswith("/"):
        return target / "index.html"
    if target.exists():
        return target
    if target.suffix:
        return target
    return target / "index.html"


def check(site: Path, *, site_prefix: str) -> list[str]:
    problems: list[str] = []
    pages = sorted(site.rglob("*.html"))
    if not pages:
        return [f"no rendered HTML pages found under {site}"]

    for page in pages:
        parser = LinkParser()
        parser.feed(page.read_text(encoding="utf-8", errors="replace"))
        for kind, raw in parser.links:
            target = _candidate(site, page, raw, site_prefix)
            if target is None:
                continue
            if not target.exists():
                rel_page = page.relative_to(site).as_posix()
                rel_target = target.relative_to(site).as_posix() if target.is_relative_to(site) else str(target)
                problems.append(f"{rel_page}: broken {kind}={raw!r} -> {rel_target}")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("site", nargs="?", default="site")
    parser.add_argument("--site-prefix", default="reasonFirst")
    args = parser.parse_args()

    site = Path(args.site).resolve()
    problems = check(site, site_prefix=args.site_prefix)
    if problems:
        print("Rendered documentation link check failed:")
        for item in problems:
            print(f"- {item}")
        return 1

    print(f"Rendered documentation link check: OK ({sum(1 for _ in site.rglob('*.html'))} HTML pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
