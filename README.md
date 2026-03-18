<div align="center">

# 本地番茄下载器

**个人本地部署版 Fanqie 下载控制台**

一个只在本机运行的单机版番茄下载器，支持书籍解析、Cookie 登录态管理、任务队列和 TXT 导出。

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![macOS](https://img.shields.io/badge/macOS-Local_App-000000?logo=apple&logoColor=white)](https://www.apple.com/macos/)
[![TXT Export](https://img.shields.io/badge/Export-TXT-B5651D)](#-核心能力--features)
[![Local Only](https://img.shields.io/badge/Mode-Local_Only-4C8BF5)](#-项目说明--overview)

</div>

---

## 📋 目录 / Table of Contents

- [项目说明](#-项目说明--overview)
- [参考项目](#-参考项目--reference)
- [核心能力](#-核心能力--features)
- [快速开始](#-快速开始--quick-start)
- [登录方式](#-登录方式--login-flow)
- [项目结构](#-项目结构--project-structure)

---

## 🎯 项目说明 / Overview

这个仓库不是远端网关版客户端的直接镜像，而是一个**只在自己 Mac 上部署和使用的本地版实现**。

核心目标：

- 本地解析番茄书籍页和章节页
- 本地保存登录态
- 本地维护下载队列
- 本地导出 TXT 文件

它适合个人使用，不依赖独立的远端下载网关服务。

---

## 🔗 参考项目 / Reference

分析和链路参考来自公开仓库：

- [POf-L/Fanqie-novel-Downloader](https://github.com/POf-L/Fanqie-novel-Downloader)

这里保留的是本地个人版实现，不是对参考项目源码的直接再发布。

---

## ✨ 核心能力 / Features

| 模块 | 能力 | Module | Capability |
|------|------|--------|------------|
| 🔎 入口识别 | 支持书籍页、章节页、书籍 ID、章节 ID 自动识别 | Target Parsing | Book URL, chapter URL, book ID and item ID parsing |
| 🔁 反查归一 | 章节链接可自动反查并归一到所属书籍 | Resolution | Resolve chapter links back to the correct book |
| 🔐 登录态管理 | 本地保存 Cookie，并支持手动检测失效 | Session | Cookie persistence and validation |
| 🧩 浏览器扩展 | 提供 Chrome 扩展导出 Cookie JSON | Browser Helper | Chrome extension for cookie export |
| 📥 任务队列 | 支持排队、暂停、继续、置顶、删除 | Queue | Queue, pause, resume, pin and delete |
| 📄 本地导出 | 将整本小说导出为单个 TXT 文件 | Export | Single TXT output on local disk |

---

## 🚀 快速开始 / Quick Start

### 1. 启动服务 / Start the Server

```bash
python3 server.py
```

### 2. 打开本地页面 / Open the Local Console

```text
http://127.0.0.1:18930
```

### 3. macOS 双击启动 / macOS Shortcut

也可以直接双击：

- `start.command`

---

## 🔐 登录方式 / Login Flow

公开书籍通常可以直接下载。遇到受限章节时，再导入个人登录态即可。

页面内已提供两个入口：

- `打开番茄登录页`
- `Cookie 插件下载`

推荐流程：

1. 点击 `Cookie 插件下载`
2. 在 Chrome 的 `chrome://extensions/` 中加载扩展
3. 登录番茄网页
4. 使用扩展复制 JSON
5. 回到本地控制台粘贴
6. 点击 `保存并校验登录态`

运行时数据默认保存在：

```text
data/session.json
data/jobs.json
```

这两个文件已加入 `.gitignore`，不会上传到仓库。

---

## 📂 项目结构 / Project Structure

```text
.
├── chrome_extension_fanqie_cookie/   # Chrome Cookie 导出插件
├── data/
│   ├── charset.json                  # 本地字符映射表
│   ├── jobs.json                     # 任务队列持久化文件（运行时生成）
│   └── session.json                  # 登录态持久化文件（运行时生成）
├── docs/
│   └── reverse-engineering.md        # Gateway 职责与本地替代说明
├── static/                           # 本地 Web UI
├── server.py                         # 本地服务入口
└── start.command                     # macOS 启动脚本
```

---

## 📝 说明文档 / Docs

- [docs/reverse-engineering.md](docs/reverse-engineering.md)
- [chrome_extension_fanqie_cookie](chrome_extension_fanqie_cookie)
