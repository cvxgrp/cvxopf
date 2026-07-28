"""
generate_examples_readme.py

Reads all Python scripts in the examples/ directory, extracts their docstrings
and any expected output (from a comment block or running the script), and
generates a README.md for the examples subfolder.

Usage:
    uv run scripts/generate_examples_readme.py

Run from the project root directory.
"""

import ast
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent
EXAMPLES_DIR = PROJECT_ROOT / "examples"
README_PATH = EXAMPLES_DIR / "README.md"
TIMEOUT = 60  # seconds per script

# Ordered semantic manifest. Classification is deliberately explicit: many
# examples span formulations or horizons, so their primary teaching purpose
# cannot be inferred reliably from a filename.
EXAMPLE_GROUPS = {
    "Core Formulations": [
        "case9_single_step.py",
        "case14_single_step.py",
        "case14_lossy_dc.py",
        "case9_singlenode_dc.py",
    ],
    "Formulation Comparisons": [
        "case14_ac_vs_dc.py",
        "case14_formulation_comparison.py",
    ],
    "Multistep Problems": [
        "case9_multistep_flat_load.py",
    ],
    "Storage": [
        "case9_storage_ac.py",
        "case9_storage_dc.py",
        "case9_storage_ac_24h.py",
        "case9_storage_dc_24h.py",
        "case9_storage_terminal.py",
    ],
    "Nondispatchable Generation": [
        "case9_multistep_nondispatchable_ac.py",
        "case9_nondispatchable_dc.py",
    ],
    "HVDC Transmission": [
        "case9_hvdc_ac.py",
        "case9_hvdc_dc.py",
    ],
    "Generator Costs": [
        "case30pwl_ac.py",
    ],
    "Performance and Representation": [
        "case57_sparse_vs_dense_ac.py",
        "case118_sparse_vs_dense_ac.py",
    ],
}


def _validated_groups(examples_dir: Path) -> dict[str, list[Path]]:
    """Resolve the manifest and reject missing, duplicate, or unknown files."""
    actual = {
        path.name: path
        for path in examples_dir.glob("*.py")
        if path.name != "__init__.py"
    }
    listed = [
        filename
        for filenames in EXAMPLE_GROUPS.values()
        for filename in filenames
    ]
    duplicates = sorted(
        filename
        for filename, count in Counter(listed).items()
        if count > 1
    )
    nonexistent = sorted(set(listed) - set(actual))
    unclassified = sorted(set(actual) - set(listed))

    problems = []
    if duplicates:
        problems.append(f"listed more than once: {duplicates}")
    if nonexistent:
        problems.append(f"listed but not found: {nonexistent}")
    if unclassified:
        problems.append(f"not classified: {unclassified}")
    if problems:
        raise ValueError(
            "Invalid EXAMPLE_GROUPS manifest; " + "; ".join(problems)
        )

    return {
        group: [actual[filename] for filename in filenames]
        for group, filenames in EXAMPLE_GROUPS.items()
    }


def extract_docstring(filepath: Path) -> str:
    """Extract the module-level docstring from a Python file."""
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)
        docstring = ast.get_docstring(tree)
        return docstring or "_No description provided._"
    except SyntaxError:
        return "_Could not parse file._"


def run_script(filepath: Path) -> str:
    """Run a script and capture its stdout output."""
    try:
        result = subprocess.run(
            [sys.executable, str(filepath)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=PROJECT_ROOT,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            error = result.stderr.strip()
            return f"_Script exited with errors:_\n```\n{error}\n```"
        return output if output else "_No output._"
    except subprocess.TimeoutExpired:
        return f"_Script timed out after {TIMEOUT} seconds._"
    except Exception as e:
        return f"_Could not run script: {e}_"


def slugify(text: str) -> str:
    """Convert a heading string to a GitHub-flavoured markdown anchor slug."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s]+", "-", text.strip())
    return text


def script_anchor(name: str) -> str:
    """Return the anchor GitHub will generate for a ## `filename.py` heading."""
    return slugify(name)


def generate_readme(examples_dir: Path, readme_path: Path) -> None:
    groups = _validated_groups(examples_dir)
    if not any(groups.values()):
        print("No example scripts found.")
        return

    # --- Collect output up front so we can build TOC ---
    # structure: [(group, [(script, docstring, output), ...]), ...]
    sections: list[tuple[str, list[tuple[Path, str, str]]]] = []
    for group, scripts in groups.items():
        entries = []
        for script in scripts:
            print(f"Processing {script.name}  [{group}]")
            docstring = extract_docstring(script)
            output = run_script(script)
            entries.append((script, docstring, output))
        sections.append((group, entries))

    # --- Build markdown ---
    lines = [
        "# Examples",
        "",
        "This folder contains example scripts demonstrating various features of the project.",
        "This file is auto-generated by `scripts/generate_examples_readme.py`. Do not edit manually.",
        "",
        "---",
        "",
        "## Table of Contents",
        "",
    ]

    # TOC entries
    for group, entries in sections:
        group_slug = slugify(group)
        lines.append(f"- [{group}](#{group_slug})")
        for script, _, _ in entries:
            anchor = script_anchor(script.name)
            lines.append(f"  - [`{script.name}`](#{anchor})")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Section bodies
    for group, entries in sections:
        lines.append(f"## {group}")
        lines.append("")
        for script, docstring, output in entries:
            lines.append(f"### `{script.name}`")
            lines.append("")
            lines.append(docstring)
            lines.append("")
            lines.append("#### Expected Output")
            lines.append("")
            lines.append("```")
            lines.append(output)
            lines.append("```")
            lines.append("")
        lines.append("---")
        lines.append("")

    readme_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nREADME written to {readme_path}")


if __name__ == "__main__":
    generate_readme(EXAMPLES_DIR, README_PATH)
