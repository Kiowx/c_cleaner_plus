# c_cleaner_plus

<p align="center">
  <img width="250" height="250" alt="driveclean_icon_512_circle" src="https://github.com/user-attachments/assets/f2d6399e-868c-4205-a086-65c6e3603468" />
</p>


<p align="center">
  <strong>Language</strong> ·
  <a href="README.md"><strong>简体中文</strong></a> |
  <a href="README.en.md">English</a>
</p>

---

Windows系统的C盘强力清理工具，可扫描并清理C盘中的垃圾文件以及大文件。

本项目使用 **Python + Fluent 2 Design** 编写，完全开源免费，面向 Windows 平台，支持常规垃圾清理与大文件扫描清理两种模式，同时提供GUI界面，每次启动时自动获取管理员权限、回收站/永久删除等功能，简单易操作，适合各种方面的用户使用。

<img width="1682" height="969" alt="QQ_1771643066991" src="https://github.com/user-attachments/assets/facb84b7-4e5a-47ec-82b3-2e9808f7e83a" />

---

## ✨ 功能特性

### 🔹 常规清理
- 用户临时文件（`%TEMP%`）
- 系统临时文件（`C:\Windows\Temp`）
- Windows 日志（CBS / DISM）
- 崩溃转储（Minidump / MEMORY.DMP）
- 缩略图缓存（Explorer）
- DirectX / NVIDIA Shader Cache / AMD Shader Cache（可选）
- 浏览器缓存（Edge / Chrome，可选）
- pip 下载包缓存 / .NET 包缓存
- Windows 更新缓存（可选）
- 更多详细内容可在[Releases](https://github.com/Kiowx/c_cleaner_plus/releases)内查看

支持：
- 扫描并**获取可清理大小**
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
  - 执行前确认是否清理

---

### 🔹 权限与安全
- 启动时自动检测管理员权限
- 非管理员状态下自动请求 UAC 提权
- 可选：清理前创建系统还原点（需管理员）

---

## 🖥️ 运行环境

- Windows 10 / Windows 11
- Python 3.9+（推荐 3.10 / 3.11）
- 仅支持 Windows（使用了 Windows API）

---

## 🚀 使用方法

### 方法一：从 Releases 下载（推荐）

如果你不想自己配置 Python 环境，**强烈推荐直接下载已打包好的可执行文件**：

**前往 [Releases](https://github.com/Kiowx/c_cleaner_plus/releases) 页面下载最新版：**  
https://github.com/Kiowx/c_cleaner_plus/releases

下载后：
1. **右键 `.exe` 文件 → 以管理员身份运行**
2. 按界面提示扫描并清理即可

> Releases 中提供的 `exe` 文件已包含运行环境，无需额外安装 Python。

---

### 方法二：从源码运行

```bash
git clone https://github.com/Kiowx/c_cleaner_plus.git
cd c_cleaner_plus
python main.py
