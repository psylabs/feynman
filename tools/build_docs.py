"""Build the public documentation site.

The site has two layers:

* pdoc API reference for public server modules.
* generated reference pages from source-of-truth files such as schema.sql,
  skills.yaml, suppressions.yaml, and docs/reference/decision-flows.yaml.

The build intentionally excludes private finance ingestion internals and scans
the final HTML for sensitive terms before it can be published.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GITHUB_ROOT = "https://github.com/psylabs/feynman/blob/main"

PDOC_MODULES = ("server", "!server.money")

SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Plaid integration detail", re.compile(r"\bplaid\b", re.IGNORECASE)),
    ("Plaid env var", re.compile(r"FEYNMAN_PLAID", re.IGNORECASE)),
    ("transaction CSV", re.compile(r"transactions\.csv", re.IGNORECASE)),
    ("finance export vendor", re.compile(r"\bSimplifi\b", re.IGNORECASE)),
    ("private finance module link", re.compile(r"server/(money\.py|money\.html)|server\.money", re.IGNORECASE)),
    ("absolute user path", re.compile(r"/Users/[^\\s<>'\"]+")),
    ("private tailnet host", re.compile(r"tail72bfb3|pips-mac-mini", re.IGNORECASE)),
)


class PrivacyLeakError(RuntimeError):
    """Raised when generated public docs contain sensitive local details."""


def source_link(path: str, label: str | None = None) -> str:
    return f"[{label or path}]({GITHUB_ROOT}/{path})"


def generate_schema_reference(schema_path: Path) -> str:
    """Return Markdown documentation generated from schema.sql."""
    schema_text = schema_path.read_text()
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(schema_text)
        tables = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY rowid
            """
        ).fetchall()
        indexes = conn.execute(
            """
            SELECT name, tbl_name
            FROM sqlite_master
            WHERE type = 'index' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

        out = [
            "# Database Schema",
            "",
            f"Source: {source_link('schema.sql')}",
            "",
            "This page is generated from the checked-in SQLite schema. It lists structure only; no database rows or private local data are read.",
        ]
        for (table,) in tables:
            out.extend([
                "",
                f"## {table}",
                "",
                "| Column | Type | Not null | Default | Primary key |",
                "| --- | --- | --- | --- | --- |",
            ])
            for col in conn.execute(f'PRAGMA table_info("{table}")'):
                _, name, col_type, not_null, default, pk = col
                out.append(
                    f"| {name} | {col_type or ''} | {_yes_no(not_null)} | "
                    f"{default if default is not None else ''} | {_yes_no(pk)} |"
                )

            table_indexes = [name for name, tbl in indexes if tbl == table]
            if table_indexes:
                out.extend(["", "**Indexes**", ""])
                out.extend(f"- `{name}`" for name in table_indexes)
        return "\n".join(out) + "\n"
    finally:
        conn.close()


def generate_config_reference(root: Path) -> str:
    """Return Markdown documentation generated from public config files."""
    skills_path = root / "skills.yaml"
    suppressions_path = root / "suppressions.yaml"

    skills = yaml.safe_load(skills_path.read_text()) or []
    active_suppressions = yaml.safe_load(suppressions_path.read_text()) or {}

    from server import scheduler, suppressions

    out = [
        "# Config Reference",
        "",
        f"Source: {source_link('skills.yaml')}",
        "",
        f"Source: {source_link('suppressions.yaml')}",
        "",
        f"Rule code: {source_link('server/suppressions.py')}",
        "",
        f"Scheduler code: {source_link('server/scheduler.py')}",
        "",
        "Generated from public configuration and non-sensitive scheduler constants.",
        "",
        "## Skills",
        "",
        "| Skill | Display | Target latency | Tolerance | Parameter schema |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for skill in skills:
        tolerance = (skill.get("tolerance") or {}).get("type", "")
        schema = _inline_dict(skill.get("parameter_schema") or {})
        out.append(
            f"| {skill['id']} | {skill.get('display_name', '')} | "
            f"{skill.get('target_latency_ms', '')} ms | {tolerance} | {schema} |"
        )

    out.extend([
        "",
        "## Active Suppressions",
        "",
        "| Skill | Active rule names |",
        "| --- | --- |",
    ])
    for skill_id in sorted(active_suppressions):
        names = ", ".join(active_suppressions.get(skill_id) or [])
        out.append(f"| {skill_id} | {names} |")

    out.extend([
        "",
        "Known rule names: "
        + ", ".join(f"`{name}`" for name in sorted(suppressions.REGISTRY)),
        "",
        "## Scheduler Constants",
        "",
        "Foundation skills: "
        + ", ".join(sorted(scheduler.FOUNDATION_SKILLS)),
        "",
        "Grounded skills: " + ", ".join(sorted(scheduler.GROUNDED_SKILLS)),
        "",
        "Grounded operation pools:",
        "",
    ])
    for skill_id, ops in sorted(scheduler._GROUNDED_OPS.items()):
        label = scheduler._GROUNDED_PREFIX.get(skill_id, skill_id)
        out.append(f"- {label}: {', '.join(ops)}")

    out.extend([
        "",
        "For a 12-question drill, `build_session_plan` reserves 6 grounded slots. Money gets two dedicated staples first, then remaining grounded slots are balanced by historical attempt counts across money and weather.",
    ])
    return "\n".join(out) + "\n"


def generate_decision_flows(path: Path) -> str:
    """Return Markdown documentation generated from decision-flows.yaml."""
    data = yaml.safe_load(path.read_text()) or {}
    flows = data.get("flows") or []

    out = [
        "# Decision Flows",
        "",
        f"Source: {source_link('docs/reference/decision-flows.yaml')}",
        "",
        "These pages describe decision order, inputs, outputs, and source links without adding documentation metadata to runtime code.",
    ]

    for flow in flows:
        out.extend(["", f"## {flow['title']}", "", flow.get("purpose", "")])

        sources = flow.get("sources") or []
        if sources:
            out.extend(["", "**Source links**", ""])
            for src in sources:
                out.append(f"- {source_link(src['path'], src.get('label') or src['path'])}")

        _append_named_list(out, "Inputs", flow.get("inputs") or [])
        _append_named_list(out, "Decision order", flow.get("steps") or [])
        _append_named_list(out, "Outputs", flow.get("outputs") or [])
        _append_named_list(out, "Notes", flow.get("notes") or [])

        diagram = (flow.get("mermaid") or "").strip()
        if diagram:
            out.extend(["", "```mermaid", diagram, "```"])

    return "\n".join(out) + "\n"


def assert_public_docs_safe(site_dir: Path) -> None:
    """Scan generated docs and fail on sensitive local finance details."""
    findings: list[str] = []
    for path in sorted(site_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".js", ".json", ".txt"}:
            continue
        text = path.read_text(errors="ignore")
        for label, pattern in SENSITIVE_PATTERNS:
            match = pattern.search(text)
            if match:
                rel = path.relative_to(site_dir)
                findings.append(f"{rel}: {label}: {match.group(0)!r}")
    if findings:
        raise PrivacyLeakError(
            "public docs privacy scan failed:\n" + "\n".join(findings[:20])
        )


def build(site_dir: Path) -> None:
    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.mkdir(parents=True)

    _run_pdoc(site_dir)
    _render_markdown_file(
        ROOT / "docs" / "architecture.md",
        site_dir / "architecture" / "index.html",
        "Feynman - Architecture",
    )

    reference_dir = site_dir / "reference"
    reference_dir.mkdir(parents=True, exist_ok=True)
    _render_markdown_text(
        _reference_index_markdown(),
        reference_dir / "index.html",
        "Feynman - System Reference",
    )
    _render_markdown_text(
        generate_decision_flows(ROOT / "docs" / "reference" / "decision-flows.yaml"),
        reference_dir / "decision-flows.html",
        "Feynman - Decision Flows",
    )
    _render_markdown_text(
        generate_schema_reference(ROOT / "schema.sql"),
        reference_dir / "schema.html",
        "Feynman - Database Schema",
    )
    _render_markdown_text(
        generate_config_reference(ROOT),
        reference_dir / "config.html",
        "Feynman - Config Reference",
    )
    _render_markdown_text(_landing_markdown(), site_dir / "index.html", "Feynman docs")
    assert_public_docs_safe(site_dir)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir", default=str(ROOT / "site"))
    args = ap.parse_args()

    try:
        build(Path(args.output_dir))
    except PrivacyLeakError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _run_pdoc(site_dir: Path) -> None:
    cmd = [
        sys.executable,
        "-m",
        "pdoc",
        *PDOC_MODULES,
        "--output-directory",
        str(site_dir),
        "--docformat",
        "google",
        "--logo-link",
        "https://github.com/psylabs/feynman",
        "--no-show-source",
        "--no-include-undocumented",
        "--mermaid",
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)


def _render_markdown_file(src: Path, dst: Path, title: str) -> None:
    from tools import render_docs

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(render_docs.render(src, title))


def _render_markdown_text(markdown_text: str, dst: Path, title: str) -> None:
    from tools import render_docs

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(render_docs.render_text(markdown_text, title))


def _landing_markdown() -> str:
    return """# Feynman docs

Voice-first cognitive trainer. Built on every push to main.

- [Architecture](architecture/) - topology, session lifecycle, storage, deployment.
- [Decision flows](reference/decision-flows.html) - how scheduler, generator, and retention decisions are made.
- [Config reference](reference/config.html) - generated from skills, suppressions, and scheduler constants.
- [Database schema](reference/schema.html) - generated from schema.sql.
- [API reference](server.html) - pdoc output for public server modules.
- [Source on GitHub](https://github.com/psylabs/feynman)
"""


def _reference_index_markdown() -> str:
    return """# System Reference

Generated pages derived from source-of-truth files.

- [Decision flows](decision-flows.html)
- [Config reference](config.html)
- [Database schema](schema.html)
- [API reference](../server.html)
"""


def _append_named_list(out: list[str], title: str, items: list[str]) -> None:
    if not items:
        return
    out.extend(["", f"**{title}**", ""])
    out.extend(f"- {item}" for item in items)


def _yes_no(value: object) -> str:
    return "yes" if value else "no"


def _inline_dict(data: dict) -> str:
    return ", ".join(f"{key}: {_inline_value(value)}" for key, value in data.items())


def _inline_value(value: object) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
