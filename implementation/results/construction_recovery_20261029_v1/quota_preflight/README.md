# 调用前额度读取

2026-10-28T16:39:01 UTC，通过固定 Codex CLI 0.164.0、现有 ChatGPT 登录及17891代理，实际调用官方 `account/rateLimits/read`。只发送 `initialize`、`initialized` 与额度读取，**生成请求0**；没有读取或打印认证令牌，没有重置额度、消耗额度重置积分或启动模型线程。

返回的 `codex` 窗口已用19%，窗口10080分钟，重置时刻2026-11-04T06:35:54 UTC；`rateLimitReachedType=null`，`secondary=null`。[安全结果](result.json)保存实际字段。当前暴露窗口未达到限额，不能保证具体模型生成权益、短时服务可用或排除后续429。

[实际发送请求](requests.json)与[一次性探针源码](probe_source.py)可复核；同一目录不可重跑覆盖。原始流仅在现场 `.private` 中，目录0700、文件0600、Git忽略；没有公开账号元数据。读取依据为实际查阅的[官方OpenAI Docs：Codex App Server](https://learn.chatgpt.com/docs/app-server)。
