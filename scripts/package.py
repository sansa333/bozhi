"""Build a source-only submission ZIP; never ship keys, original materials or private run data."""
import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE_DIRS = ("src", "tests", "scripts", "docs", "examples", ".github")
INCLUDE_FILES = ("README.md", "pyproject.toml", "uv.lock", ".env.example", ".gitignore",
                 "Dockerfile", ".dockerignore", "compose.yaml", "start.ps1", "requirements.txt",
                 ".gitattributes", ".editorconfig", ".python-version", "CONTRIBUTING.md", "SECURITY.md")


def build(destination: Path):
    files = []
    for name in INCLUDE_FILES:
        p = ROOT / name
        if p.is_file():
            files.append(p)
    for directory in INCLUDE_DIRS:
        parent = ROOT / directory
        if parent.exists():
            files += [p for p in parent.rglob("*") if p.is_file()
                      and not any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(parent).parts)
                      and p.suffix not in {".pyc", ".pyo"}]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(set(files)):
            relative = p.relative_to(ROOT)
            if p.name == ".env" or "private" in relative.parts:
                raise RuntimeError("Private path in package")
            archive.write(p, "shipping-bill-agent/" + relative.as_posix())
    print(f"Packaged {len(set(files))} source files: {destination.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "dist/shipping-bill-agent.zip")
    build(parser.parse_args().out)
