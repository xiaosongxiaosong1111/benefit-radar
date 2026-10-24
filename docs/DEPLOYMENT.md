# 账户版部署与维护

此文说明阿里云上的账户版部署。默认本机模式保留原来的单用户体验；公网使用账户模式，不能把无认证的本机端口直接暴露到互联网。

## 运行结构

```text
浏览器 ── HTTPS :443 / Nginx ── 127.0.0.1:44173 / Python + systemd
                              │
                              ├── accounts.sqlite3：账号、密码哈希、会话哈希、加密 Key
                              ├── master.key：密钥加密主密钥
                              └── users/<随机账号ID>/policy.sqlite3：各自的业务数据
```

当前入口为 **https://47.120.19.14/**，使用已经开放的 HTTPS 默认端口 `443`，也可填写 `https://47.120.19.14:443/`。服务器的 `8085` 已由其他网站使用，`8086` 尚未在云端放行，因此不启用；NoticeAction 继续使用 `8084`。旧域名 `benefit-radar.47-120-19-14.sslip.io` 跳转到新入口，域名证书的 ACME 验证路径继续保留。Python 的 `44173` 不对外开放。

新入口只读取既有含 `47.120.19.14` IP SAN 的 `noticeaction-ip` 证书，由原有 Certbot 任务自动续期；不修改 NoticeAction 的服务、API 配置或证书续期设置。旧域名跳转继续使用 `benefit-radar` 域名证书。更换入口须同时核对证书、Nginx `server_name` 和服务的 `--public-origin`。

同一 IP 的不同端口共享 Cookie 作用域。权益雷达使用独立的 `benefit_session` Cookie 名、Secure/HttpOnly 标志、精确 Origin（含端口）检查和 CSRF 校验，避免账户混用和跨端口写入；同 IP 的其他 HTTPS 服务仍可能收到此 Cookie，只有可信应用适合共用 IP。不同应用需要严格会话隔离时应使用不同主机名。原账户、清单和 API Key 保留；从旧域名切换到 IP 入口需重新登录。

| 位置 | 用途 |
| --- | --- |
| `/opt/benefit-radar/releases/` | 发布版本，只读代码 |
| `/opt/benefit-radar/current` | 当前发布版本链接 |
| `/opt/benefit-radar/venv/` | 独立 Python 3.11 运行环境 |
| `/var/lib/benefit-radar/` | 私有账户及业务数据，仅服务用户可读写 |
| `/etc/systemd/system/benefit-radar.service` | 后台运行与自动重启 |
| `/www/server/panel/vhost/nginx/benefit-radar.conf` | HTTPS 反向代理 |
| `/www/server/panel/vhost/nginx/benefit-radar-acme.conf` | 证书 HTTP-01 验证路径 |
| `/etc/letsencrypt/live/noticeaction-ip/` | IP 入口读取的 TLS 证书，由既有 Certbot 定时任务续期 |
| `/etc/letsencrypt/live/benefit-radar/` | 旧域名跳转使用的 TLS 证书，保留原续期路径 |

配置样例位于 [deploy/](../deploy/)。样例为当前服务器目录布局，其他服务器应调整路径后使用。

## 初始化与启动

需要 Python 3.11+、Nginx、systemd，以及账户模式的 `requirements.txt` 依赖。使用独立的 `benefitradar` 系统用户，不允许该用户修改程序代码。

```bash
/usr/bin/python3.11 -m venv /opt/benefit-radar/venv
/opt/benefit-radar/venv/bin/python -m pip install -r /opt/benefit-radar/current/requirements.txt
```

先初始化管理员，再开放注册。管理员密码通过终端遮罩输入，不放在命令参数或服务文件里：

```bash
cd /opt/benefit-radar/current
runuser -u benefitradar -- /opt/benefit-radar/venv/bin/python policy_accounts.py \
  --data-dir /var/lib/benefit-radar --create-admin admin
```

重复初始化不会重置现有管理员密码。公开注册始终创建普通账户，不能在请求里指定管理员角色。

```bash
systemctl daemon-reload
systemctl enable --now benefit-radar
/www/server/nginx/sbin/nginx -t
systemctl reload nginx
```

Python 后端只监听回环地址。Nginx 将上游 `Host` 固定为 `127.0.0.1:44173`，保留浏览器的真实 `Origin`，由账户服务与配置的 HTTPS `public_origin` 精确核对。Nginx 覆盖 `X-Real-IP`，不信任客户端自行填写的来源地址。

## 登录与元宝配置

1. 使用管理员账号登录，或通过页面注册自己的普通账户。
2. 打开右上角的账户与 API 设置，输入当前账户自己的元宝 SearchPro 服务 API Key。
3. 点击“保存并验证”。保存后先处于待验证状态，再向元宝实际发出一次公开政策搜索请求。
4. 请求成功才显示上次验证可用的时间；失败会保留明确提示。删除后，该账户不再使用该 Key。

普通账户不会回退使用管理员或服务器环境变量中的 Key。管理员也没有通过页面读取其他用户 Key 的接口。已保存的 Key 仅返回掩码，输入框不会回填原值；会话使用 HttpOnly Cookie，HTTPS 部署启用 Secure，写请求还需要 CSRF Token。

“已验证可用”代表上次请求结果，之后仍可能因密钥撤销、服务权限或账户状态变化而失败。页面不能代替腾讯云账户的实时套餐与权限查询。

## 数据权限与备份

```text
/var/lib/benefit-radar/                 0700  benefitradar:benefitradar
  accounts.sqlite3                     0600
  master.key                           0600
  users/<账号ID>/                       0700
    policy.sqlite3                     0600
```

**账户数据库与 `master.key` 必须一起备份。** 主密钥丢失或与数据库不匹配时，服务会拒绝启动，不会生成一个新密钥假装原有配置仍可用。不要把数据目录、主密钥、初始密码文件或环境文件提交到 Git；项目已忽略这些敏感文件名及默认数据目录。

本原型适合在维护窗口短暂停止服务后备份整个私有目录，避免 SQLite 写入期间直接复制不一致的文件。备份应保存在仅管理员可访问的位置，并验证可恢复。

## 检查与更新

```bash
systemctl is-active benefit-radar
systemctl status benefit-radar --no-pager
journalctl -u benefit-radar --since '10 minutes ago' --no-pager
curl --fail http://127.0.0.1:44173/api/health
```

日志不应打印密码、Cookie、API Key 或请求体。更新时使用新发布目录，保留原发布目录和私有数据；安装依赖与验证通过后再切换 `current` 并重启。回滚代码时恢复上一版链接，不覆盖或删除账户数据。

证书续期依赖当前服务器的 `certbot-renew.timer` 和 Nginx 重载钩子。应定期检查证书日期、定时任务状态与外网 HTTPS，不能只检查后端健康接口。

## 验证范围

发布前已完成账号与 Key 的隔离、CSRF、会话撤销、缓存失效、跨标签页与延迟响应切换账号测试。完整记录见 [QA.md](../QA.md)。

服务器上线检查应再确认：匿名访问业务接口返回401、登录 Cookie 为 Secure/HttpOnly、账户 A 的清单不会出现在 B 中、主密钥和数据库权限正确、现有站点仍可访问。实际元宝授权需要在账户设置页输入有效 Key 后验证；受控测试传输不代表真实授权已经通过。
