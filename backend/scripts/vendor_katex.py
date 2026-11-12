"""Vendors KaTeX's browser files into src/banyan/export/assets/katex (design §10.2).

The HTML export inlines them, so an exported study guide renders math with no network access.
Downloads the npm tarball, checks it against the registry's sha512 integrity, and copies only the
files the export uses. Run again to upgrade: `uv run python scripts/vendor_katex.py 0.19.1`.
"""

import base64
import hashlib
import io
import json
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "src/banyan/export/assets/katex"
WANTED = ("dist/katex.min.css", "dist/katex.min.js", "dist/fonts/")


def main(version: str) -> None:
    with urllib.request.urlopen(f"https://registry.npmjs.org/katex/{version}") as r:
        dist = json.load(r)["dist"]
    algo, expected = dist["integrity"].split("-", 1)
    assert algo == "sha512", algo
    with urllib.request.urlopen(dist["tarball"]) as r:
        tarball = r.read()
    actual = base64.b64encode(hashlib.sha512(tarball).digest()).decode()
    if actual != expected:
        raise SystemExit(f"integrity mismatch for katex {version}")
    if TARGET.exists():
        shutil.rmtree(TARGET)
    (TARGET / "fonts").mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name.removeprefix("package/")
            if not member.isfile() or not name.startswith(WANTED):
                continue
            if name.startswith("dist/fonts/") and not name.endswith(".woff2"):
                continue  # the export inlines woff2 only
            data = tar.extractfile(member)
            assert data is not None
            (TARGET / name.removeprefix("dist/")).write_bytes(data.read())
    (TARGET / "VERSION").write_text(f"{version}\n{dist['integrity']}\n")
    print(f"katex {version} vendored into {TARGET}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "0.19.1")
