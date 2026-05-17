# fast_large_files

Optional Windows helper for accelerating large-file scans on NTFS drives.

The Python UI keeps the existing scanner as a fallback. This helper is only
used for drive-root scans such as `C:\` when the compiled executable is
available.

## Build

```powershell
cargo build --release --manifest-path tools/fast_large_files/Cargo.toml
```

The generated executable is:

```text
tools/fast_large_files/target/release/fast_large_files.exe
```

## Output contract

The helper prints JSON lines to stdout:

```json
{"size":123456789,"path":"C:\\path\\to\\file.bin"}
```

If the helper is missing, fails, or lacks permission to read the NTFS volume,
the Python application falls back to its original `os.scandir()` scanner.
