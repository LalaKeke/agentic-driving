# 通过本机代理访问 API

这是 HTTP/HTTPS 应用代理转发，不是整台服务器的 VPN。API 客户端需读取代理环境变量或显式配置代理。密钥仍由用户按项目配置提供，网络检查不携带 API 密钥、不进行推理调用。

## 登录节点：一个 SSH 会话同时提供终端和代理

Windows PowerShell，在项目根目录运行：

```powershell
.\hpc\connect_proxy.ps1
```

默认把服务器的 `127.0.0.1:17890` 转发到本机的 `127.0.0.1:7890`，并打开交互终端。也可以直接执行：

```powershell
ssh -tt -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -R 127.0.0.1:17890:127.0.0.1:7890 tongji-u21012
```

在该远程终端中：

```bash
cd /share/home/u21012/hzy/agentic-driving
source hpc/use_proxy.sh 17890
curl -q --proxy "$https_proxy" --http1.1 --tlsv1.2 --tls-max 1.2 \
  --connect-timeout 8 --max-time 20 -o /dev/null -w '%{http_code}\n' \
  https://api.openai.com/v1/models
```

未提供密钥时预期为 `401`，表示到达 API 服务；不代表已具备账户权限或某个模型可用。旧版 curl 的默认 TLS 握手可能失败，此时可单独用 TLS 1.2 定位问题，保持证书验证开启。Python 客户端应优先使用默认安全 TLS 上下文，按实际错误决定是否设置兼容参数。

本机代理程序及 SSH 会话都需保持运行。本机休眠、断网或 SSH 断开后代理失效；不会因为已导出环境变量就继续具有外网连接。可在服务器用 `ss -ltn '( sport = :17890 )'` 检查监听。端口被占用时选择另一个高位端口，不终止未知监听进程。

## 计算节点：必须将代理转发到实际运行的节点

登录节点的 `127.0.0.1` 不等于计算节点的 `127.0.0.1`。不要直接把登录节点的回环代理变量传入 Slurm 作业后假定其可用。

对已分配给本任务的计算节点，可从本机通过 `ProxyJump` 直接建立第二条 SSH 会话，在计算节点回环端口上接入本机代理；当前集群内部没有验证可用的免密登录，因此通过本机交互认证，不把密码写入作业。

### 提交网络验证作业（服务器终端）

```bash
cd /share/home/u21012/hzy/agentic-driving
mkdir -p logs runs
sbatch --parsable --account=p_p21012 --partition=intel --export=NONE \
  --chdir="$PWD" hpc/proxy_probe.sbatch "$PWD" 17891
```

记下新作业号。该作业仅占 1 CPU、1 GB、最多 10 分钟，在所分配节点等待 420 秒以便完成两段交互认证并建立隧道，不申请 GPU。

读取 `runs/<job_id>/compute_node.txt`、`compute_ssh_port.txt`、`compute_host_key.txt`。主机公钥由这个已认证调度路径中的作业从计算节点自己的回环 SSH 服务取得，不能用随意扫描到的外部公钥替代。

### 建立计算节点转发（本机第二个 PowerShell 窗口）

将上述文件复制到本机的临时目录。将公钥行的第一列替换为 `[实际计算节点名]:实际SSH端口`（端口为 22 时只写节点名），保存为本次节点的 known-hosts 文件。保持主机验证开启。

```powershell
.\hpc\connect_proxy.ps1 -ComputeNode 实际节点名 -NodeSshPort 实际端口 `
  -RemoteProxyPort 17891 -ComputeKnownHostsFile .\work\node_known_hosts
```

按提示分别完成登录节点与计算节点认证。在计算节点终端中确认转发已建立后标记本任务就绪：

```bash
touch /share/home/u21012/hzy/agentic-driving/runs/<job_id>/forward_ready
```

作业随后检查 GitHub HTTPS 与无凭据的 API 端点，结果为 `runs/<job_id>/proxy_validation.json`。用 `sacct` 确认任务状态与退出码。验证结束后退出这条计算节点 SSH 会话；登录节点会话可继续使用。

对正式 API 作业，先取得其实际分配节点，再建立同样的隧道，并在**实际客户端进程**中设置 `http_proxy/https_proxy` 等变量。隧道必须覆盖整个调用时段；多节点任务需要逐节点安排连接。当前探针不是生产多节点隧道编排器。

## 监听和凭据边界

- 两类转发都只绑定 `127.0.0.1`，不修改 sshd、GatewayPorts 或防火墙，不暴露为全网代理。
- 回环地址并不隔离同一服务器的其他本地用户；共享节点上不能把它视为只有自己可访问的认证边界。只在需要的时段维持连接，转发代理不承载 API 密钥存储。
- 不自动修改用户的全局 SSH 配置、全局代理、授权密钥或任何登录凭据。
- SSH 转发是否获准仍取决于集群策略。服务端明确禁止时报告限制，不改 sshd 或绕过限制。
