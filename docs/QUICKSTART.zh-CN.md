# Cursor Session Hub v1.1（1.1.0）

无需安装 Python、Node.js 或 Rust。先使用离线本地库；准备好服务器后，再登录团队空间，手动同步选定记录。

下载：[Release v1.1.0](https://github.com/QingQ-zijin/cursor-session-hub/releases/tag/v1.1.0)。

## 选择安装包

| 电脑 | 文件 |
|---|---|
| Windows 64位 | Cursor.Session.Hub_1.1.0_x64-setup.exe |
| Mac M系列芯片 | Cursor.Session.Hub_1.1.0_aarch64.dmg |
| Mac Intel芯片 | Cursor.Session.Hub_1.1.0_x64.dmg |

Windows 双击安装程序。Mac 打开对应 DMG，将应用拖入 Applications，再从 Applications 启动。Mac 安装目标为 macOS 13 或更新版本；本次实际构建与启动检查分别运行于 macOS 14 Apple Silicon 和 macOS 15 Intel。

这些是未商业签名、未经 Apple 公证的团队内测包。系统可能显示未知开发者提示；确认文件来自本项目后，可使用系统提供的单应用“仍要运行”或“仍要打开”选项。无需关闭系统整体安全检查。Windows 安装器会检查 WebView2 运行时，缺少时首次安装可能需要联网；运行时安装完成后，本地阅读可以离线使用。

## 第一次使用

1. 打开“本地记录”，点击“本机记录”按工作区筛选 Cursor 原始标题；也可导入或拖入 JSONL、Markdown、HTML、PDF 文件。
2. 点击“同步到本地库”。等待任务结束后打开记录；默认只展开最近3轮，较早轮次点击后加载；已展开轮次会自动读取完整问答，工具详情展开后自动读取完整内容。
3. 本地模式无需账号。首次启动会尝试迁移旧查看器的导入文件与自定义标题，不自动上传。
4. 如需团队协作，在服务器上初始化管理员，由管理员创建邀请链接；成员通过网页完成注册。
5. 在客户端填写服务器地址并登录。选择会话，检查待同步清单和图片，再点击同步服务器。
6. 网页“团队空间”可以按成员查看记录、评论和收藏。会话更新后手动再次同步；阅读端点击“有更新”才切换版本。

服务器从源码目录部署，命令和备份恢复步骤见 [deploy/README.md](../deploy/README.md)。源码包包含 Dockerfile、Compose 配置、数据库迁移及备份脚本；不包含聊天内容、账号凭证或数据库。尚未租服务器时，可以在本机 Docker 上先用 `http://localhost:8088` 测试。

## 本地数据与磁盘空间

Windows 数据在 `%LOCALAPPDATA%\CursorSessionHub`；Mac 数据在 `~/Library/Application Support/CursorSessionHub`。升级安装保留独立的数据目录；旧查看器和原始 Cursor 数据保留原位。

默认保留至少5GiB且至少10%的空闲磁盘空间。任一条件不满足，新导入或上传会暂停，已有索引仍可阅读。可以先释放空间，或通过 `CSH_HOME` 将应用数据放到空间足够的磁盘。

容量很大的个人磁盘如需在内测时降低百分比阈值，可在启动进程的环境中显式设置 `CSH_MIN_FREE_RATIO=0.01`，保留5GiB绝对下限。该设置不会自动应用到其他电脑或服务器；服务器继续使用约定的默认保护阈值。所有环境配置必须在应用启动前设置，已运行的应用需完全退出后重启。

## 构建与校验

各版本的实际构建与验证结果见 [GitHub Actions](https://github.com/QingQ-zijin/cursor-session-hub/actions) 和 [验收记录](VERIFICATION.md)。交付目录中的 `SHA256SUMS.txt` 可用于验证文件未在传输中改变。Windows 可运行 `Get-FileHash -Algorithm SHA256 -LiteralPath '安装包完整路径'`；Mac 可运行 `shasum -a 256 '安装包完整路径'`。

本版已验证实际本机 Docker 的双账号同步、阅读、评论和导出，及5成员阅读与2上传同时进行。三平台安装包均通过目标系统的冻结解析器和安装后启动检查；团队实际电脑的完整使用流程仍应在内测中确认。详细结果见 [VERIFICATION.md](VERIFICATION.md)。

保留上游 MIT 许可证与来源说明，分发时请同时保留 `LICENSE` 和 `NOTICE.md`。

## 检查更新

从本版开始，客户端启动后和运行中每6小时自动检测 GitHub 正式版。点击左下角版本号可手动检查、阅读更新说明并下载对应安装包。更新由你确认安装，本地库不会清空。0.1.2及更早的内测版需要先手动安装本版一次。

## 文档导入

PDF 按页提取文字，不将 PDF 原文冒充为 Cursor 对话。扫描页没有文字层时会显示诊断，需要先 OCR；加密 PDF 请先解密。PDF 上限 64 MiB，单个文本块上限 8 MiB，后台解析超限或出错时原始文件保留。HTML/Markdown 保留可识别的问答角色，普通文档作为原文记录导入。
