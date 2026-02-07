#c_cleaner_plus

<img width="1773" height="1275" alt="34ee5a6741a442feb96c74dd5bb15d70" src="https://github.com/user-attachments/assets/52d3a1ac-ac35-4524-956a-868b4f9e5941" />

<p align="center">
  <strong>Language</strong> ·
  <a href="README.md">简体中文</a> |
  <a href="README.en.md"><strong>English</strong></a>
</p>

---

A powerful C drive cleaner for Windows, capable of scanning and cleaning junk files and large files on the C drive.

This project is written in Python + Tkinter, completely free, designed for the Windows platform. It supports two modes: regular junk cleaning and large file scanning/cleaning. It also provides a GUI interface, automatically acquires administrator privileges on startup, and offers features like Recycle Bin/Permanent Deletion. It's simple and easy to use, suitable for users of all levels.

---

✨ Features

🔹 Regular Cleaning

· User Temporary Files (%TEMP%)
· System Temporary Files (C:\Windows\Temp)
· Windows Logs (CBS / DISM)
· Crash Dumps (Minidump / MEMORY.DMP)
· Thumbnail Cache (Explorer)
· DirectX / NVIDIA Shader Cache / AMD Shader Cache
· Browser Cache (Edge / Chrome, optional)
· pip Download Package Cache / .NET Package Cache
· Windows Update Cache (optional)
· More details can be found in Releases

Supports:

· Scanning and obtaining the cleanable size
· Selective cleaning by checking items
· Safe items are checked by default

---

🔹 Large File Scanning

· Scan Large Files on C Drive
· Customizable:
  · Minimum file size threshold (MB)
  · Maximum number of files to list
· Sorted display (by size)
· Selective deletion for individual files

Large file list supports:

· File name / Size / Full path display
· Right-click menu:
  · Copy Path
  · Open Containing Folder
  · Locate in Explorer
· Double-click to quickly check/uncheck

---

🔹 Cleaning Modes

· Normal Mode: Deleted files go to the Recycle Bin (recoverable)
· Powerful Mode: Permanent deletion, bypassing the Recycle Bin
  · Enabled by default
  · Confirmation required before cleaning

---

🔹 Permissions & Safety

· Automatically detects administrator privileges on startup
· Automatically requests UAC elevation if not running as admin
· Optional: Create a system restore point before cleaning (requires admin)

---

🔹 GUI

· Graphical interface (Tkinter)
· Split layout:
  · Regular cleaning area / Large file list area
  · Fixed ratio layout (default 55% / 45%)
· Adaptive window sizing
· Log output and progress bar display
· Ability to stop/cancel operations at any time

---

🖥️ Runtime Environment

· Windows 10 / Windows 11
· Python 3.9+ (recommended 3.10 / 3.11)
· Windows only (uses Windows APIs)

---

🚀 How to Use

Method 1: Download from Releases (Recommended)

If you don't want to set up a Python environment yourself, it is highly recommended to download the pre-packaged executable directly:

Go to the Releases page to download the latest version:
https://github.com/Kiowx/c_cleaner_plus/releases

After downloading:

1. Right-click the .exe file → Run as administrator
2. Follow the on-screen prompts to scan and clean

The .exe file provided in Releases includes the runtime environment; no need to install Python separately.

---

Method 2: Run from Source Code (Currently Not Supported)

```bash
git clone https://github.com/Kiowx/c_cleaner_plus.git
cd c_cleaner_plus
python main.py
```
