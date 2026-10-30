"""Launch a frozen-app startup check without requesting interactive UAC.

The probe copies the release executable beside its dependencies and changes
only its XML execution-level value to asInvoker, keeping the PE and appended
PyInstaller archive offsets intact. Application bytecode is unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pefile


def as_invoker_copy(executable: Path, target: Path) -> None:
    data = bytearray(executable.read_bytes())
    pe = pefile.PE(data=bytes(data))
    changed = False
    for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if entry.id != 24:  # RT_MANIFEST
            continue
        for name in entry.directory.entries:
            for language in name.directory.entries:
                resource = language.data.struct
                start = pe.get_offset_from_rva(resource.OffsetToData)
                end = start + resource.Size
                manifest = bytes(data[start:end])
                original = b'level="requireAdministrator"'
                replacement = b'level="asInvoker"'
                replacement += b" " * (len(original) - len(replacement))
                if original in manifest:
                    data[start:end] = manifest.replace(original, replacement, 1)
                    changed = True
    pe.close()
    if not changed:
        raise ValueError("Expected a requireAdministrator application manifest")
    target.write_bytes(data)


def smoke(bundle: Path, report_path: Path, timeout: int = 45) -> dict:
    bundle = bundle.resolve(strict=True)
    executable = bundle / "c_cleaner_plus.exe"
    probe = bundle / ("startup-probe-" + uuid.uuid4().hex + ".exe")
    try:
        as_invoker_copy(executable, probe)
        environment = dict(os.environ)
        environment.pop("QT_QPA_PLATFORM", None)
        with tempfile.TemporaryDirectory(prefix="cleaner-probe-") as temporary:
            result_path = Path(temporary) / "startup.json"
            process = subprocess.Popen([str(probe), "--startup-check", str(result_path)], cwd=bundle,
                                       env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15)
                process.wait(timeout=10)
                raise RuntimeError("Frozen startup timed out; the probe process was stopped") from None
            if not result_path.is_file():
                raise RuntimeError(f"Frozen startup produced no diagnostic report (exit {code})")
            report = json.loads(result_path.read_text(encoding="utf-8"))
            if code != 0 or not report.get("success"):
                raise RuntimeError(json.dumps(report, ensure_ascii=False, indent=2))
            expected = (bundle / "_internal" / "PySide6" / "Qt6Core.dll").resolve()
            if Path(report["loaded_dlls"]["Qt6Core.dll"]).resolve() != expected:
                raise RuntimeError("Startup loaded Qt from outside the bundle")
            icu = report["loaded_dlls"].get("icuuc.dll", "")
            if icu and Path(icu).resolve().parent != (Path(os.environ["WINDIR"]) / "System32").resolve():
                raise RuntimeError("Qt loaded a non-system ICU DLL: " + icu)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return report
    finally:
        if probe.exists():
            probe.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="Directory containing the release executable and _internal")
    parser.add_argument("--report", type=Path, default=Path("build/frozen-startup.json"))
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()
    try:
        report = smoke(args.bundle, args.report, args.timeout)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
