<p align="center">
  <img width="250" height="250" alt="driveclean_icon_512_circle" src="https://github.com/user-attachments/assets/f2d6399e-868c-4205-a086-65c6e3603468" />
</p>

<h1 align="center">C Cleaner Plus</h1>

<p align="center">
  Windows 清理工具 · Python + Fluent 2 Design
</p>

<p align="center">
  <a href="https://github.com/Kiowx/c_cleaner_plus/releases">
    <img src="https://img.shields.io/github/v/tag/Kiowx/c_cleaner_plus?style=flat-square&color=green&label=Version" alt="Version">
  </a>
  <a href="https://docs.cus.cc.cd/">
    <img src="https://img.shields.io/badge/_文档-docs.cus.cc.cd-12B7F5?style=flat-square&logo=read-the-docs&logoColor=white" alt="Documentation">
  </a>
  <a href="https://t.me/kyu649">
    <img src="https://img.shields.io/badge/Telegram-交流群-26A5E4?style=flat-square&logo=telegram&logoColor=white" alt="Telegram">
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

面向 Windows 的全盘清理工具，覆盖常规清理、大文件扫描、更多清理、应用强力卸载、定时任务和规则商店等常见场景。  
项目基于 **Python + PySide6 + Fluent Widgets** 开发，提供图形界面，支持管理员提权、深浅色主题、侧边栏样式切换，以及按需自定义、导入远程规则包。

<img width="1800" height="1050" alt="QQ_1777555111436" src="https://github.com/user-attachments/assets/cd32b8bd-f39d-4090-b3ae-4d03d2a76722" />

## 功能概览

### 常规清理

- 清理临时文件、系统日志、缩略图缓存、浏览器缓存、包管理器缓存等常见项目
- 支持估算可清理大小
- 支持勾选执行、自定义规则、拖拽排序和规则导入导出
- 支持回收站删除和永久删除

### 大文件扫描

- 支持多分区扫描
- 支持自定义最小文件大小和结果数量
- 支持按大小、名称、路径排序
- 支持按项勾选删除

### 更多清理

- 重复文件查找
- 空文件夹扫描
- 无效快捷方式清理
- 卸载注册表扫描
- 右键菜单清理

### 应用强力卸载

- 支持标准卸载和强力卸载两种方式
- 标准卸载后可继续扫描残留
- 强力卸载支持文件、注册表、服务、计划任务清理
- 内置高风险拦截和分级保护

### 定时任务

- 支持每天、每周、每小时、每分钟、登录时触发
- 支持自定义间隔
- 可执行常规清理、空文件夹清理、无效快捷方式清理、卸载注册表清理和应用标准卸载
- 支持开机自启

### 规则商店与界面设置

- 支持远程规则包下载与导入
- 当前规则方向包括：
  - 通用规则
  - 国产软件
  - 开发工具
  - AI 工具
  - 影音与创作工具
  - 安卓与移动开发工具
  - 设计建模 / 3D / CAD
  - 游戏平台
- 支持深色 / 浅色 / 跟随系统主题
- 支持横向 / 纵向侧边栏切换
- 支持更新通道与日志导出

## 运行环境

- Windows 10 / Windows 11
- Python 3.9+
- 仅支持 Windows
- 部分功能需要管理员权限

## 使用方法

### 1. 下载发行版

前往 [Releases](https://github.com/Kiowx/c_cleaner_plus/releases) 下载最新版，解压后直接运行即可。  
如果系统提示权限不足，建议右键以管理员身份运行。

### 2. 从源码运行

```bash
git clone https://github.com/Kiowx/c_cleaner_plus.git
cd c_cleaner_plus
pip install -r requirements.txt
python main.py
```

## 配置与规则

- 常规配置默认保存在 `configs` 目录
- 自定义规则、排序状态、全局设置会自动保存
- 规则商店支持远程规则包下载与导入
- 常见配置文件包括：
  - `configs\\cdisk_cleaner_config.json`
  - `configs\\cdisk_cleaner_custom_rules.json`
  - `configs\\cdisk_cleaner_global_settings.json`
- 自定义规则格式说明见文档：<https://docs.cus.cc.cd/guide/config.html>

## 免责声明

- 清理和卸载操作有风险
- 建议在高风险操作前创建还原点或备份重要数据
- 请按需勾选规则，不要删除不确定的项目
