# c_cleaner_plus

**Language：** [简体中文](#-c盘强力清理工具windows) | [English](#-c-cleaner-plus-windows)

---

# 🧹 C盘强力清理工具（Windows）

Windows系统的C盘强力清理工具，可扫描并清理C盘中的垃圾文件以及大文件。

本项目使用 **Python + Tkinter** 编写，面向 Windows 平台，支持常规垃圾清理与大文件扫描清理两种模式，提供图形界面、管理员权限自动提权、回收站/永久删除控制等功能，适合普通用户与进阶用户使用。

👉 **如果你觉得这个项目有用，欢迎点个 Star 支持一下，感谢！**

---

## ✨ 功能特性

### 🔹 常规清理
- 用户临时文件（`%TEMP%`）
- 系统临时文件（`C:\Windows\Temp`）
- Windows 日志（CBS / DISM）
- 崩溃转储（Minidump / MEMORY.DMP）
- 缩略图缓存（Explorer）
- DirectX / NVIDIA Shader Cache
- 浏览器缓存（Edge / Chrome，可选）
- Windows 更新缓存（可选）

支持：
- 扫描并**估算可清理大小**
- 按项目勾选执行
- 安全项默认勾选

---

### 🔹 大文件扫描
- 扫描 **C盘大文件**
- 自定义：
  - 最小文件大小阈值（MB）
  - 最大列出数量
- 排序显示（按大小）
- 可单独勾选删除

大文件列表支持：
- 文件名 / 大小 / 完整路径显示
- 右键菜单：
  - 复制路径
  - 打开所在文件夹
  - 在资源管理器中定位
- 双击快速勾选

---

### 🔹 清理模式
- **普通模式**：删除文件进入回收站（可恢复）
- **强力模式**：永久删除，不进入回收站  
  - 默认开启  
  - 执行前强确认

---

### 🔹 权限与安全
- 启动时自动检测管理员权限
- 非管理员状态下自动请求 UAC 提权
- 可选：清理前创建系统还原点（需管理员）

---

### 🔹 UI 与体验
- 图形界面（Tkinter）
- 上下分区布局：
  - 常规清理区 / 大文件列表区
  - 固定比例布局（默认 55% / 45%）
- 自适应窗口尺寸
- 日志输出与进度条显示
- 可随时停止/取消操作

---

## 🖥️ 运行环境

- Windows 10 / Windows 11
- Python 3.9+（推荐 3.10 / 3.11）
- 仅支持 Windows（使用了 Windows API）

---

## 🚀 使用方法

### 方法一：从 Releases 下载（推荐）

如果你不想自己配置 Python 环境，**强烈推荐直接下载已打包好的可执行文件**：

👉 **前往 Releases 页面下载最新版：**  
https://github.com/Kiowx/c_cleaner_plus/releases

下载后：
1. **右键 `.exe` 文件 → 以管理员身份运行**
2. 按界面提示扫描并清理即可

> Releases 中提供的 `exe` 文件已包含运行环境，无需额外安装 Python。

---

### 方法二：从源码运行（开发者）

```bash
git clone https://github.com/Kiowx/c_cleaner_plus.git
cd c_cleaner_plus
python main.py




🧹 C Cleaner Plus (Windows)

A powerful C drive cleaning tool for Windows.
It can scan and clean junk files as well as large files on the C drive.

This project is built with Python + Tkinter and designed for Windows systems.
It provides a graphical interface, automatic administrator privilege elevation, safe recycle-bin deletion or permanent deletion, and both regular cleanup and large-file scanning modes.

If you find this project useful, please consider giving it a ⭐ Star. Thanks!


✨ Features

🔹 Regular Cleanup

User temporary files (%TEMP%)

System temporary files (C:\Windows\Temp)

Windows logs (CBS / DISM)

Crash dumps (Minidump / MEMORY.DMP)

Explorer thumbnail cache

DirectX / NVIDIA shader cache

Browser cache (Edge / Chrome, optional)

Windows Update cache (optional)

Supports:

Scan and estimate reclaimable space

Select items individually

Safe items enabled by default


🔹 Large File Scanner

Scan large files on the C drive

Custom options:

Minimum file size (MB)

Maximum result count

Sort by file size

Select individual files for deletion

Large file list features:

File name / size / full path display

Right-click menu:

Copy file path

Open containing folder

Locate in File Explorer

Double-click to toggle selection


🔹 Cleanup Modes

Normal Mode: delete files to Recycle Bin (recoverable)

Force Mode: permanently delete files

Enabled by default

Requires confirmation before execution


🔹 Security & Permissions

Automatically detects administrator privileges

Requests UAC elevation if not running as admin

Optional system restore point creation (admin required)


🔹 UI & Experience

Graphical interface (Tkinter)

Split layout:

Regular cleanup / Large file list

Fixed ratio layout (default 55% / 45%)

Responsive window resizing

Progress bar and log output

Cancelable operations


🖥️ Requirements

Windows 10 / Windows 11

Python 3.9+ (recommended 3.10 / 3.11)

Windows only (uses Windows APIs)


🚀 Usage

Option 1: Download from Releases (Recommended)

If you don’t want to set up a Python environment, download the prebuilt executable:

👉 Download the latest release:
https://github.com/Kiowx/c_cleaner_plus/releases

Steps:

Right-click the .exe file and choose Run as administrator

Follow the UI instructions to scan and clean

> The executable includes all dependencies. Python installation is not required.


Option 2: Run from Source

git clone https://github.com/Kiowx/c_cleaner_plus.git
cd c_cleaner_plus
python main.py




---

