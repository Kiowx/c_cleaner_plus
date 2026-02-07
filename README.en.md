# c_cleaner_plus

<img width="1773" height="1275" alt="34ee5a6741a442feb96c74dd5bb15d70" src="https://github.com/user-attachments/assets/5f563dad-6857-471a-8657-a13a09dd6643" />


<p align="center">
  <strong>Language</strong> ·
  <a href="README.md">简体中文</a> |
  <a href="README.en.md"><strong>English</strong></a>
</p>

---

A powerful C drive cleaning tool for Windows.  
It can scan and clean junk files as well as large files on the system drive (C:).

This project is built with **Python + Tkinter** and is completely free.  
Designed specifically for the Windows platform, it supports both **standard junk cleanup** and **large file scanning/cleanup** modes.  
It provides a graphical user interface (GUI), automatically requests administrator privileges on startup, and supports recycle bin or permanent deletion options.

Simple to operate and suitable for users of all levels.

---

## ✨ Features

### 🔹 Standard Cleanup
- User temporary files (`%TEMP%`)
- System temporary files (`C:\Windows\Temp`)
- Windows logs (CBS / DISM)
- Crash dumps (Minidump / MEMORY.DMP)
- Thumbnail cache (Explorer)
- DirectX / NVIDIA Shader Cache
- Browser cache (Edge / Chrome, optional)
- Windows Update cache (optional)

Supports:
- Scanning and **calculating reclaimable disk space**
- Executing cleanup by selected items
- Safe items enabled by default

---

### 🔹 Large File Scan
- Scan **large files on the C drive**
- Customizable options:
  - Minimum file size threshold (MB)
  - Maximum number of files to list
- Sorted display (by file size)
- Individual selection and deletion support

The large file list supports:
- File name / size / full path display
- Right-click context menu:
  - Copy file path
  - Open containing folder
  - Locate in File Explorer
- Double-click to quickly toggle selection

---

### 🔹 Cleanup Modes
- **Normal Mode**: Files are moved to the Recycle Bin (recoverable)
- **Force Mode**: Permanently deletes files without using the Recycle Bin  
  - Enabled by default  
  - Confirmation required before execution

---

### 🔹 Permissions & Safety
- Automatically checks for administrator privileges on startup
- Requests UAC elevation if not running as administrator
- Optional: Create a system restore point before cleanup (requires admin)

---

### 🔹 GUI
- Graphical interface based on Tkinter
- Vertical split layout:
  - Standard cleanup area / Large file list area
  - Fixed ratio layout (default 55% / 45%)
- Responsive window resizing
- Log output and progress bar display
- Operations can be stopped or canceled at any time

---

## 🖥️ Runtime Environment

- Windows 10 / Windows 11
- Python 3.9+ (3.10 / 3.11 recommended)
- Windows only (uses Windows-specific APIs)

---

## 🚀 Usage

### Method 1: Download from Releases (Recommended)

If you don’t want to configure a Python environment yourself,  
**it is highly recommended to download the pre-built executable directly**.

**Download the latest version from the Releases page:**  
https://github.com/Kiowx/c_cleaner_plus/releases

After downloading:
1. **Right-click the `.exe` file → Run as administrator**
2. Follow the on-screen instructions to scan and clean

> The executable provided in Releases is fully packaged and does not require a separate Python installation.

---

### Method 2: Run from Source(Currently Unsupported)

```bash
git clone https://github.com/Kiowx/c_cleaner_plus.git
cd c_cleaner_plus
python main.py
