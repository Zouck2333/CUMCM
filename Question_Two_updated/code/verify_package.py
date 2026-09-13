"""Check the relative-path SHA256 inventory captured when the package was assembled."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads((root / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    failures = []
    for name, entry in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            failures.append({"file": name, "reason": "missing or outside package"})
            continue
        data = path.read_bytes()
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            failures.append({"file": name, "reason": "size or SHA256 changed"})
    print(json.dumps({"status": "FAIL" if failures else "PASS", "files_checked": len(manifest["files"]), "failures": failures}, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
