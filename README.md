# 本地番茄下载器

一个只在本机运行的个人版番茄下载器，提供本地书籍解析、Cookie 登录态管理、下载任务队列和 TXT 导出。

## 参考项目

本项目的分析与接口参考自公开仓库：

- [POf-L/Fanqie-novel-Downloader](https://github.com/POf-L/Fanqie-novel-Downloader)

这个仓库不是对参考项目的直接改造，而是基于公开信息做的本地单机版实现，目标是只在自己的 Mac 上部署和使用。

## 当前能力

- 支持书籍页链接、章节页链接、书籍 ID、章节 ID 自动识别
- 支持把章节链接自动反查为对应书籍
- 支持本地保存登录态，并检测登录态是否失效
- 支持粘贴原始 Cookie，或直接粘贴 Chrome 插件导出的 JSON
- 支持下载任务排队、暂停、继续、置顶、删除
- 支持将整本小说导出为单个 TXT 文件

## 目录结构

```text
.
├── chrome_extension_fanqie_cookie/   Chrome Cookie 导出插件
├── data/
│   ├── charset.json                  本地字符映射表
│   ├── jobs.json                     任务队列持久化文件（运行时生成）
│   └── session.json                  登录态持久化文件（运行时生成）
├── docs/
│   └── reverse-engineering.md        网关职责和本地替代方案说明
├── static/                           本地 Web UI
├── server.py                         本地服务入口
└── start.command                     macOS 双击启动脚本
```

## 运行方式

```bash
cd local_fanqie_personal
python3 server.py
```

启动后访问：

```text
http://127.0.0.1:18930
```

macOS 也可以直接双击 `start.command` 启动。

## 登录方式

公开书籍通常可以直接下载。遇到受限章节时，再导入个人登录态即可。

页面里提供了两个入口：

- `打开番茄登录页`
- `Cookie 插件下载`

推荐流程：

1. 点击 `Cookie 插件下载`
2. 在 Chrome 的 `chrome://extensions/` 中加载解压后的扩展
3. 登录番茄网页
4. 点击扩展，复制导出的 JSON
5. 回到本地控制台，粘贴到登录态输入框
6. 点击 `保存并校验登录态`

运行时数据默认保存在：

```text
data/session.json
data/jobs.json
```

这两个文件已经加入 `.gitignore`，不会上传到仓库。

## 说明文档

- 网关职责和旧客户端下载链路说明见 [docs/reverse-engineering.md](docs/reverse-engineering.md)
- Chrome 插件源码在 [chrome_extension_fanqie_cookie](chrome_extension_fanqie_cookie)
