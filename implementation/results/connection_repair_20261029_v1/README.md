# 连接修复与实际验证

**当前显式代理路径已验证可用：新独立预算2/2，纯文本和10图输入的GPT-6.1极短诊断均正常返回。** 已消除项目连接配置歧义，并固定预检/生成使用同一份子进程环境。本轮驾驶请求0，没有重发旧失败请求、续跑六窗或转移旧余额。历史偶发断连的底层原因仍未充分记录，不能把两次成功写成“长期不再断连”。

## 已查明及未查明

|检查|实测结果|能说明什么|
|---|---|---|
|父环境六个proxy变量|均为`http://127.0.0.1:17890`；正常账号网络权限下端口连接拒绝|直接继承会失败；但旧驾驶runner已强制17891，不能据此解释旧批次断连|
|17891无认证HEAD|一次TLS连接超时，随后HTTP/1.1和HTTP/2均收到接口405|代理链路曾短时握手失败；405证明到达接口，不是模型生成或认证通过|
|系统CA的CONNECT/TLS|8/8通过，证书校验开启，TLS1.3/h2|当时代理到目标的证书与握手可用，不保证所有后续连接|
|Python默认CA诊断|默认路径指向不存在的`/home/hzy/anaconda3/ssl`，8次证书失败；改用现有系统CA后通过|这是Python探针的环境问题；没有证据将其追认为历史Codex RustCLI失败原因|
|两份旧失败日志|turn开始后5.873s、7.219s，模型delta均0，HTTP状态未知|未触发450s总截止或300s流空闲边界；具体代理/TLS/DNS底层cause未落盘|

[历史日志定位](log_diagnosis.json)、[环境审查](config_review.json)、[HEAD对照](network_head_matrix.json)、[正确CA的8次TLS结果](tls_connect_system_ca.json)均保留。早期HEAD工具的`000`状态未能解析成JSON，失败保留curl退出码和原错误，不补造耗时；它不影响后续成功探针的原值。

官方配置区分HTTP重试、SSE重试及流空闲超时，见[OpenAI Docs](https://learn.chatgpt.com/docs/config-file/config-reference)。旧请求已传入双零provider重试，但CLI仍通知外层准备重连；适配器遇首个错误即终止。不能把provider配置0等同于全部服务内部HTTP次数为0。本轮没有放宽这一停止策略或增加自动重试。

## 最小修改

- `codex_adapter.subscription_environment()`新增显式`DRIVING_PROXY_URL`和`DRIVING_CA_BUNDLE`，也支持对应关键字参数。显式代理统一六种大小写变量，并清除该子进程的NO_PROXY/no_proxy；继续剥离三个API-key环境变量。无新配置时保留旧行为，不改变其他项目或父shell环境。
- 显式代理要求HTTP(S)、主机和端口，拒绝凭据/路径/查询参数，错误不回显敏感URL。CA必须是现存文件，仅指定可信证书束，不关闭证书或主机名校验；未安装/修改系统CA或全局DNS。
- `PilotSubscriptionAgent`在构造时捕获一次环境，预检与RPC复用同一个snapshot，并记录净化后的端点/CA配置。后续父环境变化不会悄悄把生成切回17890。
- 新增`check_codex_route.py`：一次CONNECT和可信TLS握手，无账号、无HTTP生成负载、无重试；失败明确分到代理TCP、CONNECT或TLS阶段。

环境补丁提交`70fe8f3`，snapshot提交`1b50730`。T1–T4、场景构造、几何/运动、安全间距、业务Schema、提示词和旧六窗证据原字节保持；传输源码变化需要在下一次执行登记中明确记录，不能绕过旧源码冻结检查。

## 用户授权的两个真实诊断

|请求|实际输入/原生输出|结果|
|---|---|---|
|1 文本|极短连接指令＋两字段Schema；`{"ok":true,"diagnostic":"connection"}`|PASS，约26.660s；环境补丁版本70fe8f3|
|2 图片|原普通直行窗10张原图＋同一极短输出要求；原生JSON同上|PASS，约33.125s；snapshot版本1b50730|

固定官方CLI0.164.0、现有ChatGPT登录及`gpt-6.1-sol`，解析别名一致，实际服务快照未暴露。两次各恰1个turn/start与完成通知，错误/工具/fallback/重连通知均0；私有连接日志确认使用17891。它们是**连接诊断**，没有发送驾驶提示或业务输出Schema，也没有调用驾驶`decide`、四工具或执行器，不能计作驾驶成功或图像理解评价。

[文本原生结果](generation_probe_01_text/result.json)、[图片原生结果](generation_probe_02_images/result.json)、[独立最终复核](independent_review_final.json)和请求/净化事件保留。10张图SHA与原采集匹配；原生输出原样保存，没有mock、字段修补或降级。生成测试使用debug级连接日志，原始流/账号状态留在0700/0600忽略目录，不发布凭据。内部HTTP尝试数仍标unknown，不从多个TCP连接数推算生成次数。

[预算登记](generation_budget_activation.json)为独立最多2次、失败计入、无旧余额继承；现已2/2并停止。[耗尽检查](budget_exhaustion_check.json)验证第三次会在创建transport、认证读取或网络前被拒绝，未发送第三个请求。

## 验证与用法

[39项连接/旧adapter/protocol测试](connection_regression.txt)全部通过；[15项wire/预算边界回归](transport_boundary_regression.txt)全部通过。均为本地mock/人工测试，不增加真实模型分子。覆盖旧默认兼容、代理大小写/bypass统一、API-key剥离、父环境不变、坏代理/CA拒绝、snapshot固定及原预算/来源边界。

先在当前服务器正常终端运行零生成检查：

```bash
cd /data2/hzy/MAP/agentic-driving
.venv/bin/python implementation/experiments/check_codex_route.py \
  --proxy http://127.0.0.1:17891 \
  --ca-bundle /etc/ssl/certs/ca-certificates.crt
```

交付版本已[实际运行通过](route_checker_actual.json)。返回`TLS_VERIFIED`仅说明这一次路由/握手通过；不会调用模型或替代生成验证。若返回FAILED，读取failed_stage定位，勿以关闭证书校验或重发驾驶请求掩盖失败。

后续获授权的模型任务使用如下**进程级配置**，`.env.example`也有示例；`.env`不会被Python自动读取：

```bash
export DRIVING_PROXY_URL=http://127.0.0.1:17891
export DRIVING_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
# 然后运行另行登记的任务；本轮不启动新的驾驶调用。
```

不要复制旧诊断根目录重置2次额度。六窗旧批次仍为6/12已结束、后三窗未调用；后续续跑需保留原账本、窗口与算法，并明确登记本次连接层源码变化。当前结果是“连接可用且配置固定”，旧间歇TLS握手问题是否彻底消除仍没有足够证据。
