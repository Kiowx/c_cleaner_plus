<p align="center">
  <img width="250" height="250" alt="driveclean_icon_512_circle" src="https://github.com/user-attachments/assets/f2d6399e-868c-4205-a086-65c6e3603468" />
</p>

<h1 align="center">C Cleaner Plus</h1>

<p align="center">
  Windows C 盘强力清理工具 · 基于 Python + Fluent 2 Design
</p>


<p align="center">
  <a href="https://github.com/Kiowx/c_cleaner_plus/releases">
    <img src="https://img.shields.io/github/v/tag/Kiowx/c_cleaner_plus?style=flat-square&color=green&label=Version" alt="Version">
  </a>
  <a href="https://docs.cus.cc.cd/">
    <img src="https://img.shields.io/badge/_文档-docs.cus.cc.cd-12B7F5?style=flat-square&logo=read-the-docs&logoColor=white" alt="Documentation">
  </a>
  <a href="https://qm.qq.com/q/xE1xw9wP7M">
    <img src="https://img.shields.io/badge/QQ 交流群 - 点击加入-12B7F5?style=flat-square&logo=tencent-qq&logoColor=white" alt="QQ Group">
  </a>
</p>
<p align="center">
  <a href="README.md"><strong>简体中文</strong></a> ·
  <a href="README.en.md">English</a>
</p>


---

Windows 系统的 C 盘强力清理工具，可扫描并清理 C 盘中的垃圾文件、大文件、重复文件及系统残留。

本项目使用 **Python + Fluent 2 Design** 编写，完全开源免费，面向 Windows 平台，支持常规垃圾清理、大文件扫描、重复文件查找、空文件夹清理、无效快捷方式清理及注册表清理等多种模式，同时提供 GUI 界面，每次启动时自动获取管理员权限、回收站/永久删除等功能，简单易操作，适合各种方面的用户使用。

<img width="1682" height="969" alt="QQ_1772186747866" src="https://github.com/user-attachments/assets/866ccfb3-9295-4c97-9ec6-0c9a4248726c" />

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
- 自定义清理规则

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

#### 右键菜单清理
- 深度扫描系统关键注册表位置
- 列出并清理多余、失效或不需要的右键扩展项
- 支持递归删除，包含子项的注册表键也可彻底清理

---

### 应用强力卸载
提供两条路线

- 标准卸载  
  调用软件自带卸载程序，卸载结束后可继续深度扫描残留

- 强力卸载  
  面向顽固软件的强力清除流程  
  可尝试解除进程、服务与驱动锁定  
  使用系统命令强删注册表项  
  强制删除残留文件与目录  
  对内核锁定文件可安排重启后删除

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

pip install -r requirements.txt

# 以管理员身份运行
python main.py
```
---

## 📁 配置文件说明

常见配置文件位于本机以下路径

* `%LOCALAPPDATA%\cdisk_cleaner_config.json`
  用于保存常规清理的勾选状态与拖拽排序记忆

* `%LOCALAPPDATA%\cdisk_cleaner_custom_rules.json`
  用于保存自定义清理规则，独立于界面偏好

* `%LOCALAPPDATA%\cdisk_cleaner_global_settings.json`
  用于保存全局设置，例如自动保存与更新通道

* `%TEMP%\cdisk_cleaner_cache.json`
  用于保存硬盘类型检测缓存，可在设置页一键刷新

### 自定义规则格式

详见：https://docs.cus.cc.cd/guide/config.html

## 更新通道配置

在系统设置界面可配置更新通道：

| 通道 | 说明 |
|------|------|
| **稳定版** | 经过充分测试的版本，推荐普通用户使用 |
| **测试版** | 更新较快，可能包含新功能，适合尝鲜用户 |

# 贡献指南

## 代码贡献

1. Fork 本项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

## 问题反馈

- 遇到问题请在 GitHub Issues 中提交
- 请详细描述问题现象、复现步骤、系统环境
- 附上相关截图或日志文件

## 免责声明

本工具仅供学习和个人使用，清理操作存在一定风险：

- 建议在清理前**创建系统还原点**
- 请勿随意删除不明确的大文件
- 注册表清理前请**备份注册表**
- 作者不对任何数据丢失承担责任
- 使用本工具即表示您同意自行承担风险
