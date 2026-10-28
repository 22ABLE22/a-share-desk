# 外网访问指南（Cloudflare Tunnel）

> 目标：让手机在 4G/5G 下也能访问本机的分析台，且**不花钱、不备案、不需要公网 IP**。
>
> 原理：`cloudflared` 从你电脑主动连到 Cloudflare，Cloudflare 给你一个
> `https://xxx.trycloudflare.com` 地址。全程没有开放任何入站端口。
>
> 实测环境：Windows + Python 3.12 + 本项目 `.venv`（2026-10-28 验证）

---

## 为什么需要这个

服务默认只绑定 `127.0.0.1`（回环地址），**网卡层面拒绝外部连接**，所以：

- 同一 WiFi 下的手机也打不开
- 即使改成 `0.0.0.0`，仍是 `http://` 明文 + 裸 IP → **浏览器通知会失效**
  （Notification API 要求安全上下文，只有 `127.0.0.1` 有 localhost 例外）

Cloudflare Tunnel 同时解决这两点：给了**主机名**，也给了 **HTTPS**。

---

## 前置检查

确认服务在跑：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
```

应返回 `{"ok":true,"app":"a-share-desk","auth_enabled":false}`。

---

## 第 1 步：启用访问令牌

> **为什么必须做**：隧道地址是**公开可达**的。项目原本零认证，
> 任何人拿到地址就能读你的真实持仓与成本，还能通过写接口增删。

### 1.1 生成令牌

```powershell
-join ((48..57) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
```

复制输出（约 32 位），**存好**——它只显示这一次。

### 1.2 带令牌重启服务

```powershell
cd E:\Users\Admin\github\a-share-desk

.\stop.bat

$env:ASHARE_DESK_TOKEN = '把刚才的令牌粘在这里'

.\start.bat
```

看到 `[OK] Running silently` 即成功。

> `start.bat` 会继承当前窗口的环境变量，**不需要手动跑 uvicorn**（已实测）。
> 它还会自动打开浏览器——但首次请用下面 1.4 的带令牌地址。

### 1.3 验证

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health -Headers @{'X-API-Token'='你的令牌'}
```

应看到 `"auth_enabled":true`。

再验证无令牌会被拒：

```powershell
Invoke-WebRequest http://127.0.0.1:8765/api/portfolio
```

应报 **401**。

### 1.4 浏览器登录一次

```
http://127.0.0.1:8765/#token=你的令牌
```

能打开界面即成功。令牌会存进 `localStorage`，地址栏里的片段会被自动清除，
之后正常访问 `http://127.0.0.1:8765` 即可。

---

## 第 2 步：安装 cloudflared

**新开一个** PowerShell 窗口：

```powershell
winget install --id Cloudflare.cloudflared
```

装完**关闭并重开** PowerShell（让 PATH 生效），然后验证：

```powershell
cloudflared --version
```

> 若 `winget` 不可用，可从 Cloudflare 官网下载 `cloudflared-windows-amd64.exe`，
> 重命名为 `cloudflared.exe` 放到任意目录，用完整路径调用。

---

## 第 3 步：启动隧道

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

窗口会滚动输出，找到类似这一行：

```
https://xxxx-xxxx-xxxx.trycloudflare.com
```

**这就是你的公网地址。** 窗口**不要关闭**，关掉隧道就断。

### 首次访问

```
https://xxxx-xxxx-xxxx.trycloudflare.com/#token=你的令牌
```

之后同一浏览器会自动记住令牌。

---

## 日常使用

每次开机需要**两个窗口**：

| 窗口 | 命令 |
|---|---|
| A | `$env:ASHARE_DESK_TOKEN='你的令牌'` 然后 `.\start.bat` |
| B | `cloudflared tunnel --url http://127.0.0.1:8765` |

注意：**每次重启 cloudflared，公网地址都会变**（快速隧道的特性）。
地址变了就重新复制新地址，令牌不用换。

---

## 费用

| 项目 | 费用 |
|---|---|
| Cloudflare Tunnel | **免费**，不限流量 |
| HTTPS 证书 | 免费（自动） |
| `*.trycloudflare.com` 子域名 | 免费 |
| 备案 | **不需要** |
| 公网 IP | **不需要** |

---

## 常见问题

**手机打不开？**
1. 确认第 3 步的窗口还开着
2. 确认地址是 `https://` 开头，且带了 `#token=`
3. 令牌错会返回 401 —— 检查是否复制完整（无空格）

**提示「未授权」？**
令牌变了或没带上。用 `#token=` 的地址重新打开一次。

**`cloudflared` 命令找不到？**
装完必须重开 PowerShell；或改用完整路径调用。

**`start.bat` 提示 `[FAIL] Not ready`？**
已修复。旧版本的健康探测把 401 当成"服务没起来"，
而启用令牌后 `/api/health` 必然返回 401 —— 导致服务其实正常却报失败。
现版本把 401 也视为"服务已就绪"（服务能回 401 就说明它活着）。
若仍遇到，请确认用的是最新版 `start.bat`。

**能长期挂着吗？**
可以，但电脑必须开着、两个窗口都不能关。睡眠会断。

**控制台（dash.cloudflare.com）要配什么？**
**快速隧道不需要任何配置**，也不用登录。控制台留空即可。

---

## 安全提示

- 隧道地址**公开可达**，务必先完成第 1 步的令牌
- 令牌等同于密码，不要发到聊天记录或提交进仓库
- `.gitignore` 已排除 `todo.md`/`done.md`/`data/*.json`，令牌不要写进任何被跟踪的文件
- 不用时关掉隧道窗口即可彻底断开
