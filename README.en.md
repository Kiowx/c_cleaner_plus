# c_cleaner_plus

<p align="center">
  <img width="250" height="250" alt="driveclean_icon_512_circle" src="https://github.com/user-attachments/assets/f2d6399e-868c-4205-a086-65c6e3603468" />
</p>


<p align="center">
  <strong>Language</strong> ·
  <a href="README.md"><strong>简体中文</strong></a> |
  <a href="README.en.md"><strong>English</strong></a>
</p>

---

A powerful C Drive cleanup tool for Windows systems, capable of scanning and cleaning junk files and large files on the C Drive.

This project is built with **Python + Fluent 2 Design**, completely free and open-source, designed for the Windows platform. It supports two modes: standard junk cleanup and large file scanning/cleanup. It features a GUI, automatically requests administrator privileges on startup, offers Recycle Bin/Permanent Delete options, and is easy to operate, suitable for users of all levels.

<img width="1682" height="969" alt="QQ_1771643066991" src="https://github.com/user-attachments/assets/facb84b7-4e5a-47ec-82b3-2e9808f7e83a" />

---

## ✨ Features

### 🔹 Standard Cleanup
- User Temp Files (`%TEMP%`)
- System Temp Files (`C:\Windows\Temp`)
- Windows Logs (CBS / DISM)
- Crash Dumps (Minidump / MEMORY.DMP)
- Thumbnail Cache (Explorer)
- DirectX / NVIDIA Shader Cache / AMD Shader Cache (Optional)
- Browser Cache (Edge / Chrome, Optional)
- pip Download Cache / .NET Package Cache
- Windows Update Cache (Optional)
- More details can be found in the [Releases](https://github.com/Kiowx/c_cleaner_plus/releases)

Supports:
- Scan and **estimate cleanable size**
- Execute based on selected items
- Safe items selected by default

---

### 🔹 Large File Scan
- Scan **large files on C Drive**
- Customizable:
  - Minimum file size threshold (MB)
  - Maximum number of files listed
- Sorted display (by size)
- Individually selectable for deletion

Large file list supports:
- File name / Size / Full path display
- Right-click menu:
  - Copy Path
  - Open Containing Folder
  - Locate in Explorer
- Double-click to quickly select

---

### 🔹 Cleanup Modes
- **Normal Mode**: Deleted files go to Recycle Bin (recoverable)
- **Force Mode**: Permanently delete, bypass Recycle Bin  
  - Enabled by default  
  - Confirm before cleanup

---

### 🔹 Permissions & Safety
- Automatically checks administrator privileges on startup
- Automatically requests UAC elevation if not running as admin
- Optional: Create system restore point before cleanup (requires admin)

---

## 🖥️ Requirements

- Windows 10 / Windows 11
- Python 3.9+ (Recommended 3.10 / 3.11)
- Windows Only (uses Windows API)

---

## 🚀 Usage

### Method 1: Download from Releases (Recommended)

If you prefer not to configure the Python environment yourself, **we highly recommend downloading the packaged executable directly**:

**Go to the [Releases](https://github.com/Kiowx/c_cleaner_plus/releases) page to download the latest version:**  
https://github.com/Kiowx/c_cleaner_plus/releases  

After downloading:
1. **Right-click the `.exe` file → Run as administrator**
2. Follow the interface prompts to scan and cleanup

> The `exe` file provided in Releases includes the runtime environment; no additional Python installation is required.

---

### Method 2: Run from Source

```bash
git clone https://github.com/Kiowx/c_cleaner_plus.git  
cd c_cleaner_plus
python main.py
```
