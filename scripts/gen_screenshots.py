#!/usr/bin/env python3
"""Generate SVG/PNG screenshots for yt-agent README.

Uses Rich's SVG export capability to produce clean terminal screenshots.
Run with: uv run python scripts/gen_screenshots.py
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.text import Text

REPO_ROOT = Path(__file__).parent.parent
SCREENSHOTS_DIR = REPO_ROOT / "assets" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Pattern to strip /Users/<username>/ paths from output
USERNAME_PATTERN = re.compile(r"/Users/[^/\s]+/")


def redact_paths(text: str) -> str:
    """Replace /Users/<username>/ with ~/"""
    return USERNAME_PATTERN.sub("~/", text)


def run_command(cmd: list[str], cwd: str | None = None) -> str:
    """Run a shell command and return its combined output (stdout + stderr)."""
    env = os.environ.copy()
    env["FORCE_COLOR"] = "1"
    env["COLUMNS"] = "90"

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or str(REPO_ROOT),
        env=env,
    )
    # Combine stdout and stderr; prefer stdout if non-empty
    combined = result.stdout
    if result.stderr and not combined:
        combined = result.stderr
    elif result.stderr:
        combined = combined + result.stderr
    return redact_paths(combined.rstrip())


def render_screenshot(
    name: str,
    cmd: list[str],
    title: str,
    cwd: str | None = None,
) -> None:
    """Capture command output and export as SVG + PNG."""
    print(f"  Capturing: {' '.join(cmd)}")
    output = run_command(cmd, cwd=cwd)

    # Create a recording console at a fixed width
    console = Console(
        record=True,
        width=90,
        force_terminal=True,
        color_system="truecolor",
    )

    # Feed the ANSI-escaped output into the console
    text = Text.from_ansi(output)
    console.print(text)

    # Export SVG
    svg_path = SCREENSHOTS_DIR / f"{name}.svg"
    svg = console.export_svg(title=title)
    svg_path.write_text(svg)
    print(f"  Saved SVG: {svg_path.relative_to(REPO_ROOT)}")

    # Export PNG via cairosvg (try system python3 if not available in current env)
    png_path = SCREENSHOTS_DIR / f"{name}.png"
    try:
        import cairosvg  # type: ignore[import]

        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), scale=2.0)
        print(f"  Saved PNG: {png_path.relative_to(REPO_ROOT)}")
    except ImportError:
        # Fall back to invoking cairosvg via system python3
        r = subprocess.run(
            [
                "python3",
                "-c",
                (
                    f"import cairosvg; cairosvg.svg2png("
                    f"url={str(svg_path)!r}, write_to={str(png_path)!r}, scale=2.0)"
                ),
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            print(f"  Saved PNG: {png_path.relative_to(REPO_ROOT)}")
        else:
            print(f"  PNG export failed (system python3): {r.stderr.strip()}")
    except Exception as e:
        print(f"  PNG export failed: {e}")


def main() -> None:
    print(f"Generating screenshots into {SCREENSHOTS_DIR.relative_to(REPO_ROOT)}/\n")

    uv = ["uv", "run", "yt-agent"]

    screenshots = [
        {
            "name": "history",
            "cmd": uv + ["history", "--limit", "5"],
            "title": "yt-agent history",
        },
        {
            "name": "library-channels",
            "cmd": uv + ["library", "channels"],
            "title": "yt-agent library channels",
        },
        {
            "name": "export-json",
            "cmd": uv + ["export", "--format", "json", "--limit", "2"],
            "title": "yt-agent export --format json",
        },
        {
            "name": "cleanup-dry-run",
            "cmd": uv + ["cleanup", "--dry-run"],
            "title": "yt-agent cleanup --dry-run",
        },
        {
            "name": "doctor-updated",
            "cmd": uv + ["doctor"],
            "title": "yt-agent doctor",
        },
    ]

    for item in screenshots:
        print(f"[{item['name']}]")
        try:
            render_screenshot(
                name=item["name"],
                cmd=item["cmd"],
                title=item["title"],
            )
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
        print()

    # Verbose screenshot: capture stderr separately (debug logs + table output)
    print("[verbose]")
    try:
        env = os.environ.copy()
        env["FORCE_COLOR"] = "1"
        env["COLUMNS"] = "90"

        result = subprocess.run(
            uv + ["--verbose", "library", "stats"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env=env,
        )
        # Interleave stderr (debug lines) + stdout (table)
        # Show first 15 lines of stderr then the table
        stderr_lines = result.stderr.strip().splitlines()[:8]
        verbose_output = "\n".join(stderr_lines) + "\n\n" + result.stdout.strip()
        verbose_output = redact_paths(verbose_output)

        console = Console(
            record=True,
            width=90,
            force_terminal=True,
            color_system="truecolor",
        )
        text = Text.from_ansi(verbose_output)
        console.print(text)

        svg_path = SCREENSHOTS_DIR / "verbose.svg"
        svg = console.export_svg(title="yt-agent --verbose library stats")
        svg_path.write_text(svg)
        print(f"  Saved SVG: {svg_path.relative_to(REPO_ROOT)}")

        try:
            import cairosvg

            png_path = SCREENSHOTS_DIR / "verbose.png"
            cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), scale=2.0)
            print(f"  Saved PNG: {png_path.relative_to(REPO_ROOT)}")
        except ImportError:
            print("  (cairosvg not available — SVG only)")
        except Exception as e:
            print(f"  PNG export failed: {e}")

    except Exception as e:
        print(f"  ERROR: {e}", file=sys.stderr)
    print()

    print("Done.")


if __name__ == "__main__":
    main()
