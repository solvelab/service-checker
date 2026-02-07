import re
import sys
from pathlib import Path


def _update_file(path: Path, pattern: str, new_tag: str) -> bool:
    if not path.exists():
        print(f"{path} not found, skipping")
        return False

    content = path.read_text(encoding="utf-8")
    updated = re.sub(pattern, new_tag, content)

    if updated != content:
        path.write_text(updated, encoding="utf-8")
        print(f"Updated {path} to {new_tag}")
        return True

    print(f"{path} already up to date")
    return False


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: update_docker_docs.py <version>")
        return 1

    version = sys.argv[1].strip()
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        print(f"invalid version: {version}")
        return 1

    new_tag = f"v{version}"
    tag_pattern = r"v(?:X\.Y\.Z|\d+\.\d+\.\d+)"

    _update_file(Path("DOCKER.md"), tag_pattern, new_tag)
    _update_file(
        Path("deployment.yaml"),
        r"(image:\s*ghcr\.io/[^/]+/service-checker:)(?:latest|v\d+\.\d+\.\d+)",
        rf"\g<1>{new_tag}",
    )

    version_file = Path("app/_version.py")
    version_file.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    print(f"Updated {version_file} to {version}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
