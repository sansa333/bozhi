"""Check that repository-local Markdown links and images remain portable and present."""
import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def main():
    errors = []
    pages = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md", *ROOT.glob("docs/**/*.md")]
    count = 0
    for page in pages:
        text = re.sub(r"```[\s\S]*?```", "", page.read_text(encoding="utf-8"))
        for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text):
            target = target.strip().strip("<>").split("#", 1)[0]
            if not target or target.startswith(("https://", "http://", "mailto:")):
                continue
            count += 1
            path = page.parent / unquote(target)
            if re.match(r"^[A-Za-z]:[/\\]", target) or target.startswith("/"):
                errors.append(f"{page.relative_to(ROOT)}: absolute path {target}")
            elif not path.exists():
                errors.append(f"{page.relative_to(ROOT)}: missing {target}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Documentation links verified: {len(pages)} pages, {count} local links/images")


if __name__ == "__main__":
    main()
