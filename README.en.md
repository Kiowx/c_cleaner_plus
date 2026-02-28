<p align="center">
  <img width="250" height="250" alt="driveclean_icon_512_circle" src="https://github.com/user-attachments/assets/f2d6399e-868c-4205-a086-65c6e3603468" />
</p>

<h1 align="center">C Cleaner Plus</h1>

<p align="center">
  Powerful Windows C Drive Cleaner · Built with Python + Fluent 2 Design
</p>


<p align="center">
  <a href="https://github.com/Kiowx/c_cleaner_plus/releases">
    <img src="https://img.shields.io/github/v/tag/Kiowx/c_cleaner_plus?style=flat-square&color=green&label=Version" alt="Version">
  </a>
  <a href="https://docs.cus.cc.cd/">
    <img src="https://img.shields.io/badge/_Docs-docs.cus.cc.cd-12B7F5?style=flat-square&logo=read-the-docs&logoColor=white" alt="Documentation">
  </a>
  <a href="https://t.me/kyu649">
    <img src="https://img.shields.io/badge/Telegram-Group-26A5E4?style=flat-square&logo=telegram&logoColor=white" alt="Telegram">
  </a>
  <a href="https://qm.qq.com/q/xE1xw9wP7M">
    <img src="https://img.shields.io/badge/QQ_Group-Join_Us-12B7F5?style=flat-square&logo=tencent-qq&logoColor=white" alt="QQ Group">
  </a>
</p>
<p align="center">
  <a href="README.md">简体中文</a> ·
  <a href="README.en.md"><strong>English</strong></a>
</p>


---

A powerful C drive cleanup tool for Windows that can scan and clean junk files, large files, duplicate files, and system leftovers on the C drive.

This project is built with **Python + Fluent 2 Design**, completely open source and free, designed for the Windows platform. It supports various cleanup modes including regular junk cleanup, large file scanning, duplicate file finding, empty folder cleanup, invalid shortcut cleanup, and registry cleanup. It also provides a GUI interface, automatically requests administrator privileges on startup, recycle bin/permanent deletion options, and more. Easy to operate, suitable for all types of users.

<img width="1682" height="969" alt="QQ_1772186747866" src="https://github.com/user-attachments/assets/866ccfb3-9295-4c97-9ec6-0c9a4248726c" />

---

## ✨ Features

### 🔹 Regular Cleanup
- User temporary files (`%TEMP%`)
- System temporary files (`C:\Windows\Temp`)
- Windows logs (CBS / DISM)
- Crash dumps (Minidump / MEMORY.DMP)
- Thumbnail cache (Explorer)
- DirectX / NVIDIA Shader Cache / AMD Shader Cache (optional)
- Browser cache (Edge / Chrome, optional)
- Frontend (npm / Yarn / pnpm), Backend (Go / Maven / Gradle / Cargo / Composer), and pip / .NET package cache cleanup (optional)
- Windows Update cache (optional)

Supports:
- Scan and **get cleanable size**
- Select items by checkbox to execute
- Safe items selected by default
- Custom cleanup rules

---

### 🔹 Large File Scanner
- Scan **large files across multiple partitions**
- Customizable:
  - Single/multi disk partition selection for scanning
  - Minimum file size threshold (MB)
  - Maximum number of results to display
- Sort by size
- Select individually for deletion

Large file list supports:
- File name / size / full path display
- Right-click menu:
  - Copy path
  - Open containing folder
  - Locate in File Explorer
- Double-click to quickly select

---

### 🔹 More Cleanup
- **Dropdown one-click switch** between multiple advanced cleanup modes
- Multiple specialized cleanup functions integrated into a unified interface
- Intelligently hide irrelevant options

#### Duplicate File Finder
- Uses **three-stage hash algorithm** to accurately locate duplicate files
  - Stage 1: Quick filter by file size
  - Stage 2: Partial hash comparison
  - Stage 3: Full hash confirmation
- **Smart selection** of redundant copies, keeping original files
- Supports multi-partition scanning
- Significantly reduces risk of accidental deletion

#### Empty Folder Scanner
- **Deep traversal** of specified directories
- Safely clean empty directories with no actual content
- Supports custom scan paths
- Scan results can be previewed and confirmed

#### Invalid Shortcut Cleanup
- Automatically parses `.lnk` shortcut files
- Finds invalid shortcuts where **target files are missing**
- Supports Desktop, Start Menu, Quick Launch locations
- One-click cleanup of invalid links

#### Invalid Registry Scanner
- One-click cleanup of registry leftovers from **uninstalled software**
- Scans common registry paths:
  - `HKEY_CURRENT_USER\Software`
  - `HKEY_LOCAL_MACHINE\SOFTWARE`
  - Uninstall information leftovers
- **Automatically hides** irrelevant disk selection module
- Recommended to create a system restore point before cleanup

#### Context Menu Cleanup
- Deep scan of critical system registry locations
- List and clean excess, invalid, or unwanted context menu extensions
- Supports recursive deletion, including registry keys with subkeys

---

### Powerful Application Uninstall
Provides two approaches:

- **Standard Uninstall**
  Calls the software's built-in uninstaller, continues to deeply scan for leftovers after uninstallation completes

- **Powerful Uninstall**
  Aggressive removal process for stubborn software
  Can attempt to unlock processes, services, and drivers
  Uses system commands to forcefully delete registry entries
  Forcefully deletes leftover files and directories
  Can schedule kernel-locked files for deletion after restart

---

### Cleanup Modes
- **Normal Mode**: Deleted files go to Recycle Bin (recoverable)
- **Power Mode**: Permanent deletion, does not go to Recycle Bin
  - Enabled by default
  - Confirmation required before execution

---

### Permissions & Security
- Automatically detects administrator privileges on startup
- Automatically requests UAC elevation when not running as administrator
- Optional: Create system restore point before cleanup (requires administrator)
- Comprehensive deletion warning messages to prevent accidental operations

## 🖥️ System Requirements

| Item | Requirements |
|------|------|
| Operating System | Windows 10 / Windows 11 |
| Python Version | 3.9+ (Recommended 3.10 / 3.11) |
| Platform Support | Windows only (uses Windows API) |
| Administrator Privileges | Some features require administrator privileges |

---

## 🚀 Usage

### Method 1: Download from Releases (Recommended)

If you don't want to configure the Python environment yourself, **we highly recommend downloading the pre-packaged executable directly**:

**Go to the [Releases](https://github.com/Kiowx/c_cleaner_plus/releases) page to download the latest version:**
https://github.com/Kiowx/c_cleaner_plus/releases

After downloading:
1. **Right-click the `.exe` file → Run as administrator**
2. Follow the interface prompts to scan and clean

> The `exe` files provided in Releases include the runtime environment, no need to install Python separately.

---

### Method 2: Run from Source

```bash
# Clone the project
git clone https://github.com/Kiowx/c_cleaner_plus.git
cd c_cleaner_plus

pip install -r requirements.txt

# Run as administrator
python main.py
```
---

## 📁 Configuration Files

Common configuration files are located at the following paths on your machine:

* `%LOCALAPPDATA%\cdisk_cleaner_config.json`
  Used to save regular cleanup checkbox states and drag-sort memory

* `%LOCALAPPDATA%\cdisk_cleaner_custom_rules.json`
  Used to save custom cleanup rules, independent of interface preferences

* `%LOCALAPPDATA%\cdisk_cleaner_global_settings.json`
  Used to save global settings, such as auto-save and update channel

* `%TEMP%\cdisk_cleaner_cache.json`
  Used to save disk type detection cache, can be refreshed with one click in settings page

### General Rules
* You can use the general [config](https://github.com/Kiowx/c_cleaner_plus/tree/main/config) rules provided by the project

### Custom Rule Format

See details: https://docs.cus.cc.cd/guide/config.html

## Update Channel Configuration

Update channel can be configured in the system settings interface:

| Channel | Description |
|------|------|
| **Stable** | Fully tested version, recommended for regular users |
| **Beta** | Faster updates, may include new features, suitable for early adopters |

# Contributing

## Code Contributions

1. Fork this project
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## Issue Reporting

- Please submit issues in GitHub Issues if you encounter problems
- Please describe the issue phenomenon, reproduction steps, and system environment in detail
- Attach relevant screenshots or log files

## Disclaimer

This tool is for learning and personal use only. Cleanup operations carry certain risks:

- It is recommended to **create a system restore point** before cleaning
- Do not delete unclear large files arbitrarily
- Please **backup the registry** before registry cleanup
- The author is not responsible for any data loss
- By using this tool, you agree to assume all risks at your own discretion
