# 部署与维护

运行环境：Ubuntu24.04，Docker Engine和Compose插件。建议4核4GB、100GBSSD。本地模式可在未租服务器时通过Compose测试。

## 初始化
复制 `.env.example` 为 `.env`，将数据库密码改为长随机字符串。不要提交 `.env`。

```sh
docker compose up -d --build
docker compose exec api python -m hub bootstrap-admin --username admin
```

管理员密码交互输入；然后打开 http://localhost:8088，登录后创建成员邀请链接。

## 公网部署
设置 `CSH_DOMAIN` 为已准备好的域名。大陆节点按接入商流程完成备案及域名解析；对外开放80/443，数据库不开放公网。

```sh
docker compose -f compose.yml -f compose.production.yml up -d --build
```

Caddy负责HTTPS证书，生产覆盖配置启用Secure Cookie。客户端中填写 `https://域名`，通过邀请注册链接创建账号，再在客户端登录。

## 备份和恢复
原始内容/图片在hub-data卷，账号/索引/评论在hub-db卷。二者必须一并备份。执行 `python scripts/backup.py --output /backup/某日期`，它短暂停止API及worker写入并备份，再恢复服务；备份目录应位于另一块磁盘或复制到异地。定期保留至少7份。

恢复到新的空部署使用 `python scripts/backup.py --restore /backup/某日期`。脚本会要求显式 `--confirm-restore`；先停止外部访问并备份现有部署。恢复后重置泄露或过期的成员凭证。

## 升级
先备份，再拉取新版本源代码/镜像。运行 `docker compose run --rm api python -m hub migrate` 后启动API及worker。应用数据位于卷内，安装目录不存聊天记录。

## 监控
`docker compose ps` 查看健康检查，`docker compose logs --tail 100 api worker` 查看不含会话正文的任务日志。界面显示排队/运行/暂停/失败状态；磁盘少于5GiB或10%时拒绝新上传。worker限1CPU/512MiB，API512MiB，数据库768MiB。

`compose.yml` 默认仅绑定本机8088端口，适合未租服务器阶段的验收；不要单独把旧版 `server.py` 开放公网。
