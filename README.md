# c_cleaner_plus

<p align="center">
  <img width="250" height="250" alt="driveclean_icon_512_circle" src="https://github.com/user-attachments/assets/f2d6399e-868c-4205-a086-65c6e3603468" />
</p>

<div align="center">
  <p>
    <strong>Language</strong> ·
    <a href="README.md"><strong>简体中文</strong></a> |
    <a href="README.en.md">English</a>
  </p>

<p align="center">
  <a href="https://github.com/Kiowx/c_cleaner_plus/releases">
    <img src="https://img.shields.io/github/v/tag/Kiowx/c_cleaner_plus?style=flat-square&color=green&label=Version" alt="Version">
  </a>
  <a href="https://qm.qq.com/q/xE1xw9wP7M">
    <img src="https://img.shields.io/badge/QQ 交流群 - 点击加入 -12B7F5?style=flat-square&logo=tencent-qq&logoColor=white" alt="QQ Group">
  </a>
</p>
</div>

---

Windows 系统的 C 盘强力清理工具，可扫描并清理 C 盘中的垃圾文件、大文件、重复文件及系统残留。

本项目使用 **Python + Fluent 2 Design** 编写，完全开源免费，面向 Windows 平台，支持常规垃圾清理、大文件扫描、重复文件查找、空文件夹清理、无效快捷方式清理及注册表清理等多种模式，同时提供 GUI 界面，每次启动时自动获取管理员权限、回收站/永久删除等功能，简单易操作，适合各种方面的用户使用。

<img width="1674" height="962" alt="QQ_1772002038801" src="https://github.com/user-attachments/assets/485d81c7-53cc-4d5f-b274-cccd39c7c46a" />

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
- 前端（npm / Yarn / pnpm）、后端（Go / Maven / Gradle / Cargo / Composer）及 pip / .NET 等包缓存清理（可选）
- Windows 更新缓存（可选）

支持：
- 扫描并**获取可清理大小**
- 按项目勾选执行
- 安全项默认勾选

---

### 🔹 大文件扫描
- 扫描 **多分区大文件**
- 自定义：
  - 可自行单选/多选磁盘分区扫描
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

### 🔹 更多清理
- **下拉框一键切换**多种高级清理模式
- 集成多种专项清理功能于统一界面
- 智能识别并隐藏无关选项

#### 重复文件查找
- 采用 **三阶段哈希算法** 精准定位重复文件
  - 第一阶段：文件大小快速筛选
  - 第二阶段：部分哈希比对
  - 第三阶段：完整哈希确认
- **智能勾选** 多余副本，保留原始文件
- 支持多分区扫描
- 大幅降低误删风险

#### 空文件夹扫描
- **深度遍历** 指定目录
- 安全清理无实际内容的空目录
- 支持自定义扫描路径
- 扫描结果可预览确认

#### 无效快捷方式清理
- 自动解析 `.lnk` 快捷方式
- 找出**目标文件已丢失**的失效快捷方式
- 支持桌面、开始菜单、快速启动等位置
- 一键清理无效链接

#### 无效注册表扫描
- 一键清理**已卸载软件**留下的注册表残留
- 扫描常见注册表路径：
  - `HKEY_CURRENT_USER\Software`
  - `HKEY_LOCAL_MACHINE\SOFTWARE`
  - 卸载信息残留
- **自动隐藏** 无关的磁盘选择模块
- 清理前建议创建系统还原点

---

### 清理模式
- **普通模式**：删除文件进入回收站（可恢复）
- **强力模式**：永久删除，不进入回收站
  - 默认开启
  - 执行前确认是否清理

---

### 权限与安全
- 启动时自动检测管理员权限
- 非管理员状态下自动请求 UAC 提权
- 可选：清理前创建系统还原点（需管理员）
- 完善的删除警告文案，防止误操作

## 🖥️ 运行环境

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 / Windows 11 |
| Python 版本 | 3.9+（推荐 3.10 / 3.11） |
| 平台支持 | 仅支持 Windows（使用了 Windows API） |
| 管理员权限 | 部分功能需要管理员权限 |

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
# 克隆项目
git clone https://github.com/Kiowx/c_cleaner_plus.git
cd c_cleaner_plus

# 以管理员身份运行
python main.py
```
---

## 免责声明

本工具仅供学习和个人使用，清理操作存在一定风险：

- 建议在清理前**创建系统还原点**
- 请勿随意删除不明确的大文件
- 注册表清理前请**备份注册表**
- 作者不对任何数据丢失承担责任
- 使用本工具即表示您同意自行承担风险

---

## 许可证

本项目采用 [MIT 许可证](LICENSE) 开源，您可以自由使用、修改和分发。

---

<div align="center">

**如果这个项目对你有帮助，请给一个 ⭐ Star 支持一下！**

Made with ❤️ by [Kiowx](https://github.com/Kiowx)

</div>
