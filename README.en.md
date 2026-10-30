<p align="center">
  <img width="250" height="250" alt="driveclean_icon_512_circle" src="https://github.com/user-attachments/assets/f2d6399e-868c-4205-a086-65c6e3603468" />
</p>

<h1 align="center">C Cleaner Plus</h1>

<p align="center">
  Windows cleanup utility · Python + Fluent 2 Design
</p>

<p align="center">
  <a href="https://github.com/Kiowx/c_cleaner_plus/releases">
    <img src="https://img.shields.io/github/v/tag/Kiowx/c_cleaner_plus?style=flat-square&color=green&label=Version" alt="Version">
  </a>
  <a href="https://docs.cus.cc.cd/">
    <img src="https://img.shields.io/badge/_Docs-docs.cus.cc.cd-12B7F5?style=flat-square&logo=read-the-docs&logoColor=white" alt="Documentation">
  </a>
  <a href="https://t.me/kyu649">
    <img src="https://img.shields.io/badge/Telegram-Community-26A5E4?style=flat-square&logo=telegram&logoColor=white" alt="Telegram">
  </a>
  <a href="https://qm.qq.com/q/xE1xw9wP7M">
    <img src="https://img.shields.io/badge/QQ Group-Join-12B7F5?style=flat-square&logo=tencent-qq&logoColor=white" alt="QQ Group">
  </a>
</p>

<p align="center">
  <a href="README.md">简体中文</a> ·
  <a href="README.en.md"><strong>English</strong></a>
</p>

---

A Windows full-disk cleanup utility covering regular cleanup, large file scanning, advanced cleanup tools, force uninstall, scheduled tasks, and a rule store.  
Built with **Python + PySide6 + Fluent Widgets**, it provides a GUI, administrator elevation, light/dark themes, switchable sidebar layouts, and remote rule pack import.

<img width="1800" height="1050" alt="QQ_1777555111436" src="https://github.com/user-attachments/assets/cd32b8bd-f39d-4090-b3ae-4d03d2a76722" />

## Features

### v0.8.2 Maintenance Center

- File-level cleanup previews with per-file selection, JSON export, and pre-deletion revalidation
- Shared path and wildcard exclusions, including right-click protection in scan results
- Persistent cleanup history and JSON / CSV reports, separating logical bytes from free-space changes
- Per-rule minimum age, minimum size, and keep-newest conditions, included in rule exports
- Directory drill-down, treemaps, file-type totals, and historical space-growth comparisons
- Scheduled maintenance gates for free space, idle time, running processes, cooldowns, and notifications
- Rule-pack change reviews, duplicate / broad-path checks, version history, and rollback
- Duplicate retention by path, newest, oldest, or preferred directory, with hard-link accounting
- Project dependency, virtual environment, and build-output discovery with reviewed cleanup
- Docker disk usage and cache / dangling-image cleanup; WSL virtual disk discovery and native compaction

Open **Maintenance Center** in the sidebar. Conditions gate existing Windows scheduled tasks; configure their checking frequency on the scheduled tasks page.
Data is stored in `maintenance.sqlite3` inside the active configuration directory and migrates with it.
Up to 20 complete snapshots are retained per root. Incomplete or cancelled scans are excluded from growth comparisons.
Previews are limited to 200000 entries, space scans to 200000 directories, and the visible directory tree to 10000 nodes. Narrow the scope when a limit is reached.
Moving files to the recycle bin does not immediately reclaim space. Concurrent applications can also affect measured free-space changes.
WSL compaction stops all distributions; save your work and exit Docker Desktop first. Reclamation depends on reclaimable blocks in the image.
Docker maintenance uses the [Docker CLI](https://docs.docker.com/reference/cli/docker/image/prune/); virtual-disk compaction uses Windows [CompactVirtualDisk](https://learn.microsoft.com/en-us/windows/win32/api/virtdisk/nf-virtdisk-compactvirtualdisk).

### Regular Cleanup

- Cleans common targets such as temp files, system logs, thumbnail cache, browser cache, and package manager cache
- Supports cleanup size estimation
- Supports selective execution, custom rules, drag sorting, and rule import/export
- Supports recycle bin deletion and permanent deletion

### Large File Scan

- Supports multi-drive scanning
- Supports custom minimum file size and result count
- Supports sorting by size, name, and path
- Supports selective deletion

### Advanced Cleanup

- Duplicate file scan
- Empty folder scan
- Invalid shortcut cleanup
- Uninstall registry cleanup
- Context menu cleanup

### Force Uninstall

- Supports both standard uninstall and force uninstall
- Can scan leftovers after standard uninstall
- Force uninstall can clean files, registry entries, services, and scheduled tasks
- Includes built-in risk blocking and protection tiers

### Scheduled Tasks

- Supports daily, weekly, hourly, per-minute, and logon triggers
- Supports custom intervals
- Can run regular cleanup, empty folder cleanup, invalid shortcut cleanup, uninstall registry cleanup, and standard app uninstall
- Supports auto start with Windows

### Rule Store and UI Settings

- Supports downloading and importing remote rule packs
- Current rule directions include:
  - General rules
  - Chinese apps
  - Dev tools
  - AI tools
  - Media creation tools
  - Mobile dev tools
  - 3D / CAD tools
  - Gaming platforms
- Supports dark / light / system theme
- Supports horizontal / vertical sidebar layout
- Supports update channels and log export

## Requirements

- Windows 10 / Windows 11
- Python 3.9+
- Windows only
- Some features require administrator privileges

## Usage

### 1. Download a release

Download the latest release from [Releases](https://github.com/Kiowx/c_cleaner_plus/releases):

- We recommend the `*-setup.exe` installer for a guided installation.
- For portable use, download the `*.zip` archive, extract it, and run the executable.

The application requests administrator privileges because it performs system cleanup operations.

### 2. Run from source

```bash
git clone https://github.com/Kiowx/c_cleaner_plus.git
cd c_cleaner_plus
pip install -r requirements.txt
python main.py
```

## Config and Rules

- Regular config files are stored in the `configs` directory by default
- Custom rules, sort order, and global settings are saved automatically
- The rule store supports downloading and importing remote rule packs
- Common config files include:
  - `configs\\cdisk_cleaner_config.json`
  - `configs\\cdisk_cleaner_custom_rules.json`
  - `configs\\cdisk_cleaner_global_settings.json`
- Custom rule format: <https://docs.cus.cc.cd/guide/config.html>

## Disclaimer

- Cleanup and uninstall operations carry risk
- Create a restore point or back up important data before high-risk actions
- Review selected rules carefully before deleting anything uncertain
