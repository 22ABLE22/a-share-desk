"""Repair a relocated Windows venv by rewriting embedded absolute paths.

Why this is needed
------------------
A venv is not fully relocatable on Windows. `python.exe` itself carries no
absolute path (which is why it keeps working after a rename), but:

  * `pyvenv.cfg` records the `python -m venv <path>` command used to create it
  * `Scripts/activate*` hardcode the venv location
  * every console-script launcher (`pip.exe`, `pytest.exe`, ...) is a zipapp
    whose trailer begins with a `#!<abs path to python.exe>` shebang

When the parent directory is renamed those shebangs point at a path that no
longer exists, so the launchers fail *silently* while `python -m <tool>` keeps
working — which makes the breakage easy to misdiagnose as a broken tool.

Usage
-----
    .venv\\Scripts\\python.exe repair_venv_paths.py            # auto-detect
    .venv\\Scripts\\python.exe repair_venv_paths.py --dry-run  # report only

The stale prefix is detected from the venv itself, so the script works after any
move, not just the one it was originally written for. Pass `--old <path>` to
override detection.

Safety
------
* Only files that actually contain the stale path are touched.
* Writes are atomic (temp file + os.replace) so a crash cannot truncate a file.
* Both UTF-8 and UTF-16-LE encodings are handled.
* `--dry-run` changes nothing.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Files whose *content* embeds the venv path. __pycache__ is excluded: those
# entries only carry source paths for tracebacks and are regenerated on import.
TEXT_TARGETS = [
    r".venv\pyvenv.cfg",
    r".venv\Scripts\activate",
    r".venv\Scripts\activate.bat",
    r".venv\Scripts\Activate.ps1",
    r".venv\Scripts\deactivate.bat",
    r".venv\Scripts\runxlrd.py",
]

# A `#!<drive>:\...\python.exe` shebang inside a launcher.
SHEBANG_RE = re.compile(rb"#!([A-Za-z]:\\[^\n\x00]*?pythonw?\.exe)")


def detect_stale_path(root: Path, venv: Path) -> str | None:
    """Find a venv path embedded in the files that no longer matches reality.

    Preference order: the launcher shebangs (authoritative, since those are what
    actually break), then pyvenv.cfg's recorded command.
    """
    scripts = venv / "Scripts"
    for exe in sorted(scripts.glob("*.exe")):
        m = SHEBANG_RE.search(exe.read_bytes())
        if not m:
            continue
        found = m.group(1).decode("utf-8", "replace")
        # The shebang points at <venv>\Scripts\python.exe; compare its parent venv.
        found_venv = str(Path(found).parent.parent)
        if os.path.normcase(found_venv) != os.path.normcase(str(venv)):
            return found_venv

    cfg = venv / "pyvenv.cfg"
    if cfg.exists():
        m = re.search(r"-m venv (.+)$", cfg.read_text(encoding="utf-8", errors="replace"), re.M)
        if m:
            recorded = m.group(1).strip()
            if os.path.normcase(recorded) != os.path.normcase(str(venv)):
                return recorded
    return None


def patch(path: Path, old: str, new: str, dry_run: bool) -> int:
    """Rewrite `old` -> `new` in `path`. Returns occurrences replaced."""
    data = path.read_bytes()
    total = 0
    for enc in ("utf-8", "utf-16-le"):
        old_b = old.encode(enc)
        if old_b in data:
            data = data.replace(old_b, new.encode(enc))
            total += 1
    if not total or dry_run:
        return total
    tmp = path.with_name(path.name + ".relocate-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", help="stale path to replace (default: auto-detect)")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parent
    venv = root / ".venv"
    if not venv.is_dir():
        print(f"[FAIL] no .venv under {root}")
        return 1

    old = args.old or detect_stale_path(root, venv)
    new = str(venv)
    if not old:
        print("[OK] no stale venv path found; nothing to repair")
        return 0

    print(f"stale prefix : {old}")
    print(f"current venv : {new}")
    print(f"mode         : {'dry-run' if args.dry_run else 'write'}")

    targets: list[Path] = [root / rel for rel in TEXT_TARGETS if (root / rel).exists()]
    scripts = venv / "Scripts"
    if scripts.is_dir():
        targets.extend(sorted(scripts.glob("*.exe")))

    patched, skipped, failed = [], 0, []
    for p in targets:
        try:
            n = patch(p, old, new, args.dry_run)
        except Exception as exc:  # noqa: BLE001 - report and continue
            failed.append((p, exc))
            continue
        if n:
            patched.append(p)
        else:
            skipped += 1

    for p in patched:
        print(f"  patched {p.relative_to(root)}")
    print(f"patched {len(patched)} file(s), unchanged {skipped}")

    if failed:
        print(f"[FAIL] {len(failed)} file(s) could not be patched:")
        for p, exc in failed:
            print(f"  {p}: {exc}")
        return 1

    if args.dry_run:
        print("dry-run: no files written")
        return 0

    # Stale bytecode embeds the old source paths; drop it so it is rebuilt.
    removed = 0
    for cache in venv.rglob("__pycache__"):
        for f in cache.glob("*.pyc"):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    print(f"purged {removed} stale .pyc file(s)")
    print("[OK] done - verify with: .venv\\Scripts\\pip.exe --version")
    return 0


if __name__ == "__main__":
    sys.exit(main())
