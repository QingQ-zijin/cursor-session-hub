# 部署与维护

运行环境：Ubuntu 24.04、Docker Engine 和 Compose v2 插件；备份脚本另需宿主机 Python 3.11 以上（Ubuntu 24.04 可使用 `python3`）。建议 4 核 4 GB、100 GB SSD。未租服务器时，可在本机通过 Compose 测试团队服务。

首次构建会下载基础镜像、npm 和 Python 依赖；服务器需能访问对应源，或提前配置可用镜像源。以下命令均在解压后的项目根目录执行。

## 初始化
复制 `.env.example` 为 `.env`，将数据库密码改为长随机字符串。密码会嵌入数据库连接 URL，建议使用 `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` 生成不含 URL 分隔符的值。不要提交 `.env`。

```sh
docker compose up -d --build --wait
docker compose exec api python -m hub bootstrap-admin --username admin
```

管理员密码交互输入，至少 12 个字符；然后在服务器本机打开 http://localhost:8088，登录后创建成员邀请链接。远程测试可在自己的电脑运行 `ssh -L 8088:127.0.0.1:8088 用户名@服务器地址`，再打开同一地址。

浏览器地址需要与 `.env` 的 `CSH_PUBLIC_URL` 一致；如果使用 `http://127.0.0.1:8088`，也应同步修改该配置并重新执行 `docker compose up -d --wait`。

## 公网部署
设置 `CSH_DOMAIN` 为已准备好的域名。大陆节点按接入商流程完成备案及域名解析；对外开放80/443，数据库不开放公网。

```sh
export COMPOSE_FILE=compose.yml:compose.production.yml
docker compose up -d --build --wait
```

Caddy 负责 HTTPS 证书，生产覆盖配置启用 Secure Cookie，并将 `CSH_PUBLIC_URL` 设置为 `https://域名`。客户端中填写同一地址，通过邀请注册链接创建账号，再在客户端登录。执行后续备份、恢复和升级命令时，保留上述 `COMPOSE_FILE` 设置，使工具使用同一套生产配置。

## 备份和恢复
原始内容/图片在 hub-data 卷，账号/索引/评论在 hub-db 卷。二者必须一并备份。执行 `python3 scripts/backup.py --output /backup/某日期`，它停止 API 及 worker 写入后备份，再恢复服务；输出目录必须为空。备份目录应位于另一块磁盘或复制到异地，定期保留至少 7 份。

恢复需要新的空部署：先准备源代码和 `.env`，启动容器并等待健康检查通过，此时不创建管理员或导入会话。然后执行：

```sh
docker compose up -d --build --wait
python3 scripts/backup.py --restore /backup/某日期 --confirm-restore
```

脚本核对备份校验值和目标空状态，恢复后自动启动服务；账号、评论及图片随备份恢复。同一台机器测试恢复时，使用不同的 `COMPOSE_PROJECT_NAME`、`CSH_PORT` 和独立数据卷。恢复后按需要撤销泄露或过期的成员凭证。

## 升级
先备份，再获取新版本源代码。重新构建镜像后，停止写入并执行数据库迁移：

```sh
docker compose build
docker compose stop api worker
docker compose run --rm --no-deps api python -m hub migrate
docker compose up -d --no-build --wait
```

应用数据位于卷内，安装目录不存聊天记录；只替换源代码不会更新已构建的镜像。

## 监控
`docker compose ps` 查看健康检查，`docker compose logs --tail 100 api worker` 查看不含会话正文的任务日志。界面显示排队/运行/暂停/失败状态；磁盘少于5GiB或10%时拒绝新上传。worker限1CPU/512MiB，API512MiB，数据库768MiB。

`compose.yml` 默认仅绑定本机8088端口，适合未租服务器阶段的验收；不要单独把旧版 `server.py` 开放公网。
