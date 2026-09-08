# 本机启动说明

双击桌面的 **Cursor Transcript Viewer** 快捷方式即可启动并打开查看器。

访问地址：http://127.0.0.1:3132/

也可以在此文件夹中用 PowerShell 运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-Viewer.ps1
```

本机启动器已配置为读取 Cursor 的数据库和 agent-transcripts，原始会话文件按只读方式打开。自定义标题和启动日志保存在此文件夹的 `.local` 目录。

页面左侧选择会话，Directory 可以筛选项目；Save HTML 可以导出当前会话。这里只用于查看和导出，不会将记录导回 Cursor。

## 导入单独的 JSONL

左侧搜索框下方有 **导入 JSONL** 按钮。选择 Cursor 格式的 `.jsonl` 文件后，会自动加入列表并打开。左侧被收起时，先点击左上角的 `»` 展开。

单个文件上限为 32 MB。副本保存在本程序的 `.local/imports`，原文件保留；重复导入相同内容会打开已有副本。此按钮是本机新增的功能。

## 阅读长会话

打开会话时默认仅展开最近 3 轮。点击上方 **查看更早的…轮对话**，再点某轮标题即可加载；也可以用右侧目录直接跳到指定轮次。

最多同时展开 3 轮，收起时会释放该轮的页面内容。特别长的一轮每批加载 40 条记录，底部有 **继续加载本轮内容**。工具调用仅在点开时渲染详情。

**Copy all text** 与 **Save HTML** 仍处理整条会话，不受当前展开范围限制。

关闭网页不会关闭后台服务；重启电脑后，再双击快捷方式即可。没有设置开机自启。

后台服务的进程编号记录在 `.local/server.pid`，启动日志位于 `.local/server.stdout.log` 和 `.local/server.stderr.log`。
