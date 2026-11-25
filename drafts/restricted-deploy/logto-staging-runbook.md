# G1 Logto staging runbook

- 日期：2026-11-25
- 状态：配置草案，未部署。仅 staging；不进入生产 compose 文件集。
- 决策：Sage 仓 `docs/adr/ADR-0541.md`（跨仓引用，不在本仓）
- 契约：Sage 仓 `docs/specs/2026-11-25-account-identity-contract.md`（跨仓引用，不在本仓）
- 票据：Sage 仓 `docs/plans/2026-11-25-account-unification-logto-tickets.md`（G1）

## 1. 目标与边界

在 staging 起一个自建 Logto：

- 独立 stack：`compose.logto.yaml`，project `sage-logto-staging`。
- 独立 PostgreSQL 16 容器与卷，不与 `sage-preview` 共用数据库或 role。
- 公网只开放 `https://auth.staging.sagegogo.com`；管理台只绑回环，经 SSH 隧道。
- 本批只做实例、数据库、TLS、client 与 API resource。短信、邮件、Google 连接器留给 G3。
- 不修改生产 compose、生产 DNS、官网登录代码或 App OIDC 配置。

## 2. 文件

| 文件 | 用途 |
| --- | --- |
| `compose.logto.yaml` | Logto core + 独立 PostgreSQL + 独立网络 |
| `logto-staging-nginx.conf` | 宿主 nginx 的 `auth.staging.sagegogo.com` vhost 片段 |
| `staging-logto.env.example` | Compose 与容器 env 模板；复制到仓外后填值 |
| `logto-staging-runbook.md` | 本文 |

## 3. 拓扑

```text
公网 443
  └─ 宿主 nginx：auth.staging.sagegogo.com
       └─ proxy_pass http://127.0.0.1:3101
            └─ logto core :3001
                 ├─ logto-db :5432（internal 网络）
                 └─ 管理台 :3002，仅 127.0.0.1:3102，SSH 隧道
```

- `logto` 加入 `logto_edge`（允许回环端口发布）与 `logto_backend`（连数据库）。
- `logto-db` 只加入 `logto_backend`。
- 核心端口与管理端口都只绑 `127.0.0.1`。
- 管理台不写进 nginx。

## 4. 执行前输入

以下输入缺一不可；不得用猜测值或 `latest` 代替：

1. 一台 staging 主机，Docker 29+ 与 Compose v2。
2. DNS：`auth.staging.sagegogo.com` 指向该主机。
3. 该域名的 TLS 证书与私钥；宿主 nginx 可加载。

   本机 G1 实际使用 certbot Let's Encrypt 路径 `/etc/letsencrypt/live/auth.staging.sagegogo.com`；`logto-staging-nginx.conf` 的 `/etc/sage/tls` 是腾讯云证书方案的替代路径，二选一。
4. Logto 官方镜像的固定 digest（GHCR 或 Docker Hub `svhd/logto`；1.46.0 的 digest 相同。大陆网络优先 Docker Hub）。
5. 数据库口令：`openssl rand -hex 24`，同时写入 env 的 `DB_URL` 与 secret 文件 `logto_db_pass`。
6. `SECRET_VAULT_KEK`：`openssl rand -base64 32`；与数据库备份同等托管。
7. 一个测试账号（邮箱或用户名），用于 smoke。
8. 确认官网 staging 回调域名。本文暂用 `staging.sagegogo.com`；G4 前必须定稿。

## 5. 首次部署

1. 建目录与权限：

   ```bash
   sudo install -d -m 0700 /etc/sage/logto /etc/sage/logto/secrets
   sudo install -m 0400 staging-logto.env.example /etc/sage/logto/staging-logto.env
   ```

2. 用私有编辑器填 `/etc/sage/logto/staging-logto.env`。不要用会写 shell 历史的命令行传 secret。

3. 写数据库 secret 文件：

   - 路径：`/etc/sage/logto/secrets/logto_db_pass`
   - 内容：第 5 步生成的 hex 口令，无尾随换行。
   - 权限：`0400`，owner `999:999`（PostgreSQL 容器身份）。
   - 该值与 env 的 `DB_URL` 必须完全一致。

4. 校验 Compose 展开：

   ```bash
   docker compose --env-file /etc/sage/logto/staging-logto.env \
     -f compose.logto.yaml config
   ```

   必须看到固定镜像 digest、两个回环端口、内部数据库网络。缺变量时 Compose 必须报错，不得用默认值继续。

5. 启动数据库与 Logto：

   ```bash
   docker compose --env-file /etc/sage/logto/staging-logto.env \
     -f compose.logto.yaml up -d
   ```

6. 初始化数据库。按官方 Logto CLI 执行 seed；在镜像内实测实际入口并记录：

   ```bash
   docker compose --env-file /etc/sage/logto/staging-logto.env \
     -f compose.logto.yaml run --rm --entrypoint npm logto run cli db seed -- --swe
   ```

   离线环境按 Logto CLI 关闭 HIBP 检查。seed 生成签名密钥与 cookie 密钥；立即备份数据库与 KEK。

7. 建管理员账号。seed 若提示创建，按提示完成；账号与口令不进仓库、不进聊天。

8. 宿主 nginx：把 `logto-staging-nginx.conf` 放进 `http {}` 或 include 目录，替换证书路径；执行 `nginx -t` 后 reload。不要加管理台 vhost。

9. 管理台只经隧道：

   ```bash
   ssh -L 3102:127.0.0.1:3102 <user>@<staging-host>
   ```

   浏览器打开 `http://127.0.0.1:3102`。不要在公网开放 3102。`ADMIN_ENDPOINT` 必须与隧道本地端口一致；示例值为 `http://127.0.0.1:3102`。

## 6. OIDC client 与 API resource

在管理台创建；名称固定，便于后续 G4/G5 引用：

| 对象 | 值 |
| --- | --- |
| Web client | `sage-web-staging`，Traditional Web（confidential） |
| Web redirect | `https://staging.sagegogo.com/api/auth/oidc/callback`（暂定；G4 定稿） |
| Web post logout | `https://staging.sagegogo.com/` |
| Desktop client | `sage-desktop-staging`，Native（public + PKCE） |
| Desktop redirect | `http://127.0.0.1:3000/callback` |
| API resource | `https://api.staging.sagegogo.com` |
| Scopes | `openid profile offline_access` |

规则：

- Web client 的 secret 只存 Django 服务端；G1 不写入仓库。
- Desktop client 不生成 secret。
- API resource 先建 indicator；具体 scope 与 audience 在 G5 定稿。
- 需要 refresh token 的授权请求必须带 `prompt=consent` 与 `offline_access`；只带 `offline_access` 不签发 refresh token（Logto 1.46 实测）。
- 不把 `sage-web-staging` 或 `sage-desktop-staging` 用于生产。

## 7. Smoke 与证据

必须给出真实命令输出与退出码：

```bash
curl -fsS https://auth.staging.sagegogo.com/oidc/.well-known/openid-configuration | jq -r .issuer
curl -fsS https://auth.staging.sagegogo.com/oidc/jwks | jq '.keys | length'
docker compose --env-file /etc/sage/logto/staging-logto.env -f compose.logto.yaml ps
ss -ltnp | grep -E ':(3101|3102)\b'
```

验收：

- discovery 的 `issuer` 等于 `https://auth.staging.sagegogo.com/oidc`。
- JWKS 至少 1 个 key。
- `3101` 与 `3102` 只监听 `127.0.0.1`。
- 管理台可从隧道打开；公网不可达。
- 测试账号可完成一次登录；登录页无证书错误。
- `docker compose config` 与 `ps` 输出无 secret。

## 8. 备份与恢复

备份：

```bash
docker compose --env-file /etc/sage/logto/staging-logto.env \
  -f compose.logto.yaml exec -T logto-db \
  pg_dump -U logto -d logto | gzip > /secure/backup/logto-$(date -u +%Y%m%dT%H%M%SZ).sql.gz
```

同时备份 `/etc/sage/logto/staging-logto.env` 中的 `SECRET_VAULT_KEK`，分开存放。缺 KEK 时，连接器 secret 无法恢复。

恢复演练：在独立 scratch 数据库导入 dump，启动一个临时 Logto 指向它，确认 discovery 与登录可用。未做恢复演练，不得声称备份可用。

## 9. Red 与 Green

**Red**：

- 管理台可从公网访问。
- 镜像使用 `latest` 或未固定 digest。
- `SECRET_VAULT_KEK` 缺失、写入仓库或出现在日志。
- 数据库与 `sage-preview` 共用库或 role。
- `3101`/`3102` 绑定 `0.0.0.0`。
- discovery issuer 与 `LOGTO_ENDPOINT` 不一致。
- `docker compose config` 缺变量却继续。
- 备份未做恢复演练。

**Green**：

- 上述 smoke 全部通过。
- 管理台仅隧道可达。
- 数据库与 KEK 备份、恢复演练通过。
- 测试账号登录成功。
- 仓库无 secret；证据脱敏。

## 10. 停止条件

- 需要改生产 compose、生产 DNS 或生产证书。
- 需要公开管理台。
- 需要把 secret 写入仓库、聊天或日志。
- 镜像 digest、域名或证书未定。
- 数据库不是独立实例/库/role。

## 11. 回滚

```bash
docker compose --env-file /etc/sage/logto/staging-logto.env \
  -f compose.logto.yaml down
```

默认保留 `logto_pgdata` 卷。只有明确批准后才执行 `down -v`。删除卷会丢失 staging 用户与签名密钥，必须重新 seed。

## 12. 未决输入

1. staging 域名与证书：本文用 `auth.staging.sagegogo.com`；需 DNS 与证书确认。
2. Logto 镜像 digest。
3. 官网 staging 回调域名（`staging.sagegogo.com` 暂定）。
4. API resource 的 scope 与 audience 细节（G5 定稿）。
5. 管理台访问方式：本文用 SSH 隧道；若改白名单，需单独记录。
6. seed 命令的镜像内实际入口。
7. 备份保留期与存放位置。
