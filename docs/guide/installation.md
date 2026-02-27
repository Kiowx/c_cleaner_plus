# 安装指南

## 方法一：下载可执行文件（推荐）

适合不想配置 Python 环境的用户。

### 步骤

1. 前往 [Releases](https://github.com/Kiowx/c_cleaner_plus/releases) 页面
2. 下载最新版 `.exe` 文件
3. 右键文件 → **以管理员身份运行**
4. 按界面提示扫描并清理

> ✅ Releases 中的 exe 文件已包含运行环境，无需安装 Python

## 方法二：从源码运行

适合开发者和需要自定义的用户。

### 步骤

```bash
# 1. 克隆项目
git clone https://github.com/Kiowx/c_cleaner_plus.git
cd c_cleaner_plus

# 2. 安装依赖
pip install -r requirements.txt

# 3. 以管理员身份运行
python main.py
```

### 依赖安装故障排除

```bash
# 如遇 pip 安装问题，可尝试
pip install --upgrade pip
pip install -r requirements.txt --user

# 或使用国内镜像
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 常见问题

### 启动时提示权限不足？

右键程序 → 以管理员身份运行。部分清理功能需要管理员权限。
```
