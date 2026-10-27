# GPT-6.1 会员路径实际验证记录

> 历史阶段记录，保留当时事实。后续已通过 17891 代理与项目内 CLI 0.164.0 完成真实 GPT-6.1 调用及部分 G2 评价，见 [最新实际试验](../gpt61_live/README.md)。调用成功，但完整物理闭环与 G2 未判通过。

2026-10-27，`/data2/hzy/MAP/agentic-driving`。

**实际尝试了指定模型 `gpt-6.1-sol`，但没有获得有效模型响应。当前结论是会员服务连接受阻、模型可用性未确认，不是 GPT-6.1 驾驶方案通过或不通过。**

## 认证与模型

本服务器官方 Codex CLI 为 0.147.0，`codex login status` 返回 `Logged in using ChatGPT`。使用该登录态进行测试，没有读取、导出或打印认证令牌；模型子进程移除 OPENAI_API_KEY、CODEX_API_KEY、DRIVING_API_KEY，强制 forced_login_method=chatgpt，忽略用户自定义 provider 配置，没有 API Key 回退。

按用户指定 GPT-6.1 家族，本次模型标识固定为 `gpt-6.1-sol`。本机 CLI 模型目录只列出 GPT-5.x；CLI 警告未知模型、使用后备元数据，但请求头部仍为 gpt-6.1-sol。元数据后备不等于切换推理模型，也不证明当前账号可访问 GPT-6.1。没有更换成其他模型试验。

## 实际探测与结果

1. 单张真实图像 + JSON Schema 初始探测：受限执行环境不允许 CLI 写自身状态库，客户端启动失败，没有模型响应。
2. 允许正常 CLI 缓存写入后，重试同一会员登录和模型，携带 scene-0553 的真实图像。CLI 打印请求模型 gpt-6.1-sol，随后连续记录 request timed out；180 秒后停止，无结构化结果。
3. 对 ChatGPT 公开站点的无认证网络诊断：直连 chatgpt.com:443 在 10 秒连接超时；使用服务器现有代理路径也连接失败、未收到 HTTP 响应。未创建网络代理、SSH 转发或修改网络配置。
4. 新会员适配器以最多 45 秒时限，对第一例实际 nuScenes 窗口（10 张真实图像、采集元数据、自车状态）发起调用，返回 CODEX_TRANSPORT。其余三例完成采集检查后标记 BLOCKED_MODEL_ACCESS，没有反复提交同类失败请求。批量入口退出码 2，见 [nuscenes_attempt.json](nuscenes_attempt.json)。

有效 GPT-6.1 响应 **0**；基于该模型输出的 T1–T4、轨迹核验与模拟前缀执行 **0**；G2 指标 **未计算**。请求是否到达计量服务、会员额度实际变化不可见，不能从超时推断没有消耗。没有模型场景、人工替身结果或标注数据冒充 GPT-6.1 输出。

## 已完成工程与测试

- 新增 `agentic/codex_adapter.py`：官方 CLI ChatGPT 登录适配，图像附件、output-schema、模型固定、进程组超时终止、严格响应校验、真实输出来源及图像哈希。
- 模型工作目录为仓库外的临时目录，附件重命名为 frame_NNN，输入中删除原始文件路径。关闭 shell、exec、文件图像查看、搜索、apps、plugins、multi-agent、浏览器、计算机操作等工具；任何工具调用事件都会使结果失效。运行调用没有离线真值参数。
- 成功响应才可记 source=online_model；model_id 记录显式 CLI 请求，注明 CLI 不暴露服务端 snapshot。当前无成功响应，故没有生成真实模型记录。
- nuScenes 批量入口新增 --backend codex_cli --model 参数；HTTP API 适配仍保留。相同的账号/传输/模型可用性故障会阻止其余同类调用，不替换模型。
- **76 项测试全部通过，4.864 秒**，包含真实 nuScenes 数据集成检查以及会员适配协议单测：移除 API Key、拒绝 API-key 登录、禁止工具访问、拒绝模型替换、临时图像附件、结构化解析、超时杀进程组、诊断脱敏。会员适配单测使用 mock，不是 GPT-6.1 推理实测。见 [tests.txt](tests.txt)。

## 继续执行

先使当前服务器通过可用的正常网络路径访问 chatgpt.com 的 HTTPS 服务；不需要提供 API Key，不要发送会员令牌。网络就绪后验证服务是否接受 gpt-6.1-sol，若返回账号/模型不可用错误则据实停止，不自动换模型。

```bash
cd /data2/hzy/MAP/agentic-driving
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
DRIVING_CODEX_TIMEOUT_S=180 .venv/bin/python \
  implementation/experiments/nuscenes/run_cases.py \
  --index runs/nuscenes_cases_20261027/runtime_index.json \
  --runs runs --repeats 3 --backend codex_cli --model gpt-6.1-sol
```

也可设置 DRIVING_BACKEND=codex_cli、DRIVING_MODEL=gpt-6.1-sol 后使用原有 online 命令。不要给该模式配置 API Key。网络恢复后仍须取得真实结构化输出、执行工具链、审定 GT 对象关联，才能开展 G2 评价。既有 nuScenes 数据及真值隔离保持。

官方依据：[Codex ChatGPT 认证](https://learn.chatgpt.com/docs/auth)、[非交互与结构化输出](https://learn.chatgpt.com/docs/non-interactive-mode)、[GPT-6.1 模型目录](https://developers.openai.com/api/docs/models/all)。官方文档支持该使用路径，不代表本账号权限或网络已实际通过。

## 2026-10-27 22:55 后续代理复核

用户终端已显示 ChatGPT 登录，访问首页经代理返回 `200 Connection established` 后收到 `403` / `cf-mitigated: challenge`。这是首页挑战响应，不能据此断定 Codex 推理端点被拒绝；没有保存用户输出中的 Cookie。

本次保留执行环境的现有代理设置重新请求 gpt-6.1-sol，返回 CODEX_TRANSPORT / connection_error，仍无有效模型输出。实际工具环境主机名同为 gaoteamA800，但 HTTPS_PROXY=http://127.0.0.1:17890 端口返回 ConnectionRefusedError，curl 退出码 7、没有 HTTP 响应。这与用户终端成功建立代理连接的证据不同，需要核对实际代理协议/主机/端口，暂不要求重新登录。

新的原始结构化结果、开始时刻和脱敏网络状态单独保存在 [proxy_recheck_20261027](proxy_recheck_20261027/comparison.json)。原记录不改写；未更改网络配置、认证、模型或已有数据。G2 仍未运行。
