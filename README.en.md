# c_cleaner_plus

<p align="center">
  <img width="250" height="250" alt="driveclean_icon_512_circle" src="https://github.com/user-attachments/assets/f2d6399e-868c-4205-a086-65c6e3603468" />
</p>

<div align="center">
  <p>
    <strong>Language</strong> ·
    <a href="README.md">简体中文</a> |
    <a href="README.en.md"><strong>English</strong></a>
  </p>

<p align="center">
  <a href="https://github.com/Kiowx/c_cleaner_plus/releases">
    <img src="https://img.shields.io/github/v/tag/Kiowx/c_cleaner_plus?style=flat-square&color=green&label=Version" alt="Version">
  </a>
  <a href="https://qm.qq.com/q/xE1xw9wP7M">
    <img src="https://img.shields.io/badge/QQ_Group-Join_Now-12B7F5?style=flat-square&logo=tencent-qq&logoColor=white" alt="QQ Group">
  </a>
</p>
</div>

---

A powerful C drive cleaning tool for Windows systems, capable of scanning and cleaning junk files, large files, duplicate files, and system residues on the C drive.

This project is built with **Python + Fluent 2 Design**, completely open-source and free, designed for the Windows platform. It supports various cleaning modes including regular junk cleaning, large file scanning, duplicate file finding, empty folder cleaning, invalid shortcut cleaning, and registry cleaning. It also provides a GUI interface, automatically requests administrator privileges on startup, and offers recycle bin/permanent deletion options. Simple and easy to use, suitable for all types of users.

<img width="1674" height="962" alt="QQ_1772002038801" src="https://github.com/user-attachments/assets/485d81c7-53cc-4d5f-b274-cccd39c7c46a" />

---

## ✨ Features

### 🔹 Regular Cleaning
- User temporary files (`%TEMP%`)
- System temporary files (`C:\Windows\Temp`)
- Windows logs (CBS / DISM)
- Crash dumps (Minidump / MEMORY.DMP)
- Thumbnail cache (Explorer)
- DirectX / NVIDIA Shader Cache / AMD Shader Cache (optional)
- Browser cache (Edge / Chrome, optional)
- Frontend (npm / Yarn / pnpm), Backend (Go / Maven / Gradle / Cargo / Composer), and pip / .NET package cache cleaning (optional)
- Windows Update cache (optional)

Supports:
- Scan and **get cleanable size**
- Select items by checkbox to execute
- Safe items selected by default

---

### 🔹 Large File Scanning
- Scan **large files across multiple partitions**
- Customizable:
  - Select single or multiple disk partitions to scan
  - Minimum file size threshold (MB)
  - Maximum number of results to list
- Sortable display (by size)
- Individually select files for deletion

Large file list supports:
- File name / size / full path display
- Right-click menu:
  - Copy path
  - Open containing folder
  - Locate in Explorer
- Double-click to quickly select

---

### 🔹 More Cleaning Options
- **Dropdown menu for one-click switching** between various advanced cleaning modes
- Multiple specialized cleaning functions integrated into a unified interface
- Intelligently identifies and hides irrelevant options

#### Duplicate File Finder
- Uses **three-stage hash algorithm** to accurately locate duplicate files
  - Stage 1: Quick filter by file size
  - Stage 2: Partial hash comparison
  - Stage 3: Full hash confirmation
- **Smart selection** of redundant copies, keeping original files
- Supports multi-partition scanning
- Significantly reduces risk of accidental deletion

#### Empty Folder Scanning
- **Deep traversal** of specified directories
- Safely cleans empty directories with no actual content
- Supports custom scan paths
- Scan results can be previewed and confirmed

#### Invalid Shortcut Cleaning
- Automatically parses `.lnk` shortcut files
- Identifies invalid shortcuts where **target files are missing**
- Supports Desktop, Start Menu, Quick Launch, and other locations
- One-click cleanup of invalid links

#### Invalid Registry Scanning
- One-click cleanup of registry residues left by **uninstalled software**
- Scans common registry paths:
  - `HKEY_CURRENT_USER\Software`
  - `HKEY_LOCAL_MACHINE\SOFTWARE`
  - Uninstall information residues
- **Automatically hides** irrelevant disk selection module
- **Recommended to create a system restore point before cleaning**

---

### Cleaning Modes
- **Normal Mode**: Files are deleted to Recycle Bin (recoverable)
- **Power Mode**: Permanent deletion, bypasses Recycle Bin
  - Enabled by default
  - Confirmation required before execution

---

### Permissions & Security
- Automatically checks for administrator privileges on startup
- Automatically requests UAC elevation when not running as administrator
- Optional: Create system restore point before cleaning (requires administrator)
- Comprehensive deletion warning messages to prevent accidental operations

## 🖥️ System Requirements

| Item | Requirements |
|------|------|
| Operating System | Windows 10 / Windows 11 |
| Python Version | 3.9+ (Recommended 3.10 / 3.11) |
| Platform Support | Windows only (uses Windows API) |
| Administrator Privileges | Required for some features |

---

## 🚀 Usage

### Method 1: Download from Releases (Recommended)

If you don't want to configure the Python environment yourself, **we strongly recommend downloading the pre-packaged executable file directly**:

**Go to the [Releases](https://github.com/Kiowx/c_cleaner_plus/releases) page to download the latest version:**
https://github.com/Kiowx/c_cleaner_plus/releases

After downloading:
1. **Right-click the `.exe` file → Run as administrator**
2. Follow the interface prompts to scan and clean

> The `exe` file provided in Releases includes the runtime environment, no need to install Python separately.

---

### Method 2: Run from Source Code

```bash
# Clone the project
git clone https://github.com/Kiowx/c_cleaner_plus.git
cd c_cleaner_plus

# Run as administrator
python main.py
```
---

## Disclaimer

This tool is for learning and personal use only. Cleaning operations carry certain risks:

- **Create a system restore point before cleaning**
- Do not arbitrarily delete unfamiliar large files
- **Backup the registry before registry cleaning**
- The author is not responsible for any data loss
- Using this tool indicates that you agree to assume all risks at your own discretion
