# c_cleaner_plus

<p align="center">
  <strong>Language</strong> ·
  <a href="README.en.md">简体中文</a> |
  <a href="README.md"><strong>English</strong></a>
</p>

---

# 🧹 C Cleaner Plus (Windows)

A powerful C drive cleaning tool for Windows.  
It can scan and clean junk files as well as large files on the C drive.

This project is built with **Python + Tkinter** and designed specifically for Windows systems.  
It supports regular cleanup and large-file scanning, provides a graphical user interface, automatic administrator privilege elevation, and flexible deletion modes.

👉 **If you find this project useful, please consider giving it a Star. Thank you!**

---

## ✨ Features

### 🔹 Regular Cleanup
- User temporary files (`%TEMP%`)
- System temporary files (`C:\Windows\Temp`)
- Windows logs (CBS / DISM)
- Crash dumps (Minidump / MEMORY.DMP)
- Explorer thumbnail cache
- DirectX / NVIDIA shader cache
- Browser cache (Edge / Chrome, optional)
- Windows Update cache (optional)

Supports:
- Scan and **estimate reclaimable disk space**
- Select cleanup items individually
- Safe items enabled by default

---

### 🔹 Large File Scanner
- Scan **large files on the C drive**
- Custom options:
  - Minimum file size threshold (MB)
  - Maximum number of results
- Sort results by file size
- Select individual files for deletion

Large file list features:
- File name / size / full path display
- Right-click context menu:
  - Copy file path
  - Open containing folder
  - Locate in File Explorer
- Double-click to toggle selection

---

### 🔹 Cleanup Modes
- **Normal Mode**: delete files to the Recycle Bin (recoverable)
- **Force Mode**: permanently delete files  
  - Enabled by default  
  - Confirmation required before execution

---

### 🔹 Security & Permissions
- Automatically detects administrator privileges
- Requests UAC elevation if not running as administrator
- Optional system restore point creation (administrator required)

---

### 🔹 UI & Experience
- Graphical user interface (Tkinter)
- Split layout:
  - Regular cleanup section / Large file list section
  - Fixed layout ratio (default 55% / 45%)
- Responsive window resizing
- Progress bar and detailed log output
- Operations can be stopped or canceled at any time

---

## 🖥️ Requirements

- Windows 10 / Windows 11
- Python 3.9+ (recommended 3.10 / 3.11)
- Windows only (uses Windows-specific APIs)

---

## 🚀 Usage

### Option 1: Download from Releases (Recommended)

If you do not want to set up a Python environment, you can download the prebuilt executable:

👉 **Download the latest release:**  
https://github.com/Kiowx/c_cleaner_plus/releases

Steps:
1. Right-click the `.exe` file and choose **Run as administrator**
2. Follow the UI instructions to scan and clean your system

> The executable includes all dependencies. No Python installation is required.

---

### Option 2: Run from Source

```bash
git clone https://github.com/Kiowx/c_cleaner_plus.git
cd c_cleaner_plus
python main.py
