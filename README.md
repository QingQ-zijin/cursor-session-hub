# Cursor Session Hub

面向3–5人团队的Cursor会话记录工具：本地离线阅读，选择性同步到自有服务器，在网页中按成员查看、讨论和收藏。

基于当前 cc_transcript_viewer 及其本地导入、分轮加载改动开发，保留上游MIT许可证和解析规则。旧查看器仍可运行；产品使用独立数据目录。

## 功能

- 本地免登录，发现Cursor IDE数据库、CLI数据库及JSONL；手动选择解析与同步。
- 后台单任务有界解析、磁盘索引、可恢复检查点；网页阅读和搜索不重新解析源文件。
- 默认最近3轮，最多同时展开3轮；旧轮次、工具、长文字及图片独立分页加载。
- 未知、损坏、超大记录保留为可见诊断，不静默丢失。
- 手动同步问答、代码、工具记录和关联图片，上传前可排除图片；4MiB分块、校验、断点续传。
- 团队网页登录、7天一次性邀请、成员互看已同步会话，管理员管理成员。
- 评论绑定具体版本和消息，个人收藏、中文搜索、成员/项目/日期筛选、同步历史及Markdown/HTML导出。
- 中性浅色与深色界面，Markdown与公式依赖本地打包。

## 安装包

0.1.0 的 Windows x64、Mac Apple Silicon 和 Mac Intel 安装包均已实际构建，并通过安装或挂载后的启动检查。见[安装与首次使用](docs/QUICKSTART.zh-CN.md)和[验收记录](docs/VERIFICATION.md)。

在本仓库Actions中手动运行 Build desktop installers，产物为Windows x64安装程序及Mac Apple Silicon/Intel各自的DMG。每个平台先运行冻结解析器检查，再构建和启动桌面应用。

首版为内部测试包，未使用商业代码签名或Apple公证。系统可能显示未知开发者提示。没有构建和验证成功的目标不会标为已交付。

用户不需要安装Python、Node.js或Rust；Windows安装器检查WebView2运行时。

## 本地开发

```sh
python -m venv .venv
# 激活环境后：
pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
python -m hub serve --mode local --port 8130
```

终端输出一次性本地访问地址。服务只允许回环连接；安装版通过受控native bridge访问，不向界面暴露启动令牌。

桌面构建需要对应系统的Rust/Tauri工具链：

```sh
pip install -r requirements-build.txt
npm ci
python scripts/build_sidecar.py
# 在desktop目录：
../node_modules/.bin/tauri build
```

## 云端与双账号测试

见[部署说明](deploy/README.md)。未租服务器时也能通过Docker模拟云端：

```sh
# 复制.env.example为.env，设置随机数据库密码
docker compose up -d --build
docker compose exec api python -m hub bootstrap-admin --username admin
```

访问 http://localhost:8088。生产使用 compose.production.yml 开启HTTPS和Secure Cookie；客户端填写同一服务器地址。

python scripts/verify_cloud_flow.py 使用独立测试账户和合成记录，验证真实PostgreSQL和云端worker上的同步、成员阅读、评论、图片、导出及权限。测试凭证仅存入忽略的.runtime目录。

## 数据位置与迁移

- Windows：%LOCALAPPDATA%/CursorSessionHub
- Mac：~/Library/Application Support/CursorSessionHub
- 服务器：Docker的内容数据卷和PostgreSQL卷

首次安装迁移原查看器Downloads目录中的.local/imports与自定义标题，仅入本地库，不自动上传；保留旧ID和路径别名。

CSH_HOME可指定独立数据目录。CSH_MIN_FREE_BYTES默认5GiB、CSH_MIN_FREE_RATIO默认10%；空间不足停止新上传。测试可显式降低阈值，不改变生产默认值。凭证只保存在操作系统凭证管理器。

## 验证

```sh
python -m pytest tests/test_hub_api.py tests/test_hub_remote.py tests/test_hub_ingest.py -q
npm --prefix frontend test
python tests/stress_ingest.py --help
```

资源策略见[RESOURCE_MODEL](docs/RESOURCE_MODEL.md)。真实会话、账号信息、截图、构建产物和运行数据不进入Git。
