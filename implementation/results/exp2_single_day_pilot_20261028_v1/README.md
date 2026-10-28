# 3.0.0-exp2 单窗口受控真实小试

**本轮已结束并停止。使用预算1/2，收到Schema接口错误，原生模型输出0；未修订、未换窗、未展开六窗批次。** 不能据此评价模型米制信息是否充分，也没有在线链路通过、真实车辆安全或G2通过结论。

首个计预算请求经官方Codex app-server发出，返回的错误通知为：

```text
invalid_json_schema
Invalid schema for response_format 'codex_output_schema':
In context=('properties', 'hypotheses', 'properties', 'coordinate_frame'),
schema must have a 'type' key.
```

这是当前固定Schema与结构化输出接口的兼容阻塞。没有模型原生驾驶决策/场景JSON，不能把它写成T1拒绝或模型缺少必要信息。由于用户要求接口异常停止，第二次调用没有发出；剩余一次预算不自动转成Schema修复后的兼容性探测。

## 运行冻结与来源

- 基线提交：`4648e08`；小试源码和完整注册在 **`1ef04fd`** 冻结。[基线保护检查](baseline_verification.json)核对此前封存目录和原Schema/提示词/T1–T4原字节。
- 固定原四窗中的白天行人横穿：**`ac452a60e8b34a7080c938c904b23057`**。选择理由在查看新响应前登记：原预选典型窗口、采集/独立注释链完整、历史已暴露；原始ego速度为0m/s，不称未见或全盲样本。10张原图SHA、采集SHA、原车辆及安全参数见[冻结配置](frozen_registration.json)。没有根据结果换窗。
- 输出契约 `3.0.0-exp2`，提示词 `driving_v3_exp2`，覆盖剖面 `whole_interval_sources_v3_exp2`；原5s规划、0.2s网格、0.2s前缀及1.2m中心/后轴配置不变。
- 实际客户端为官方Codex CLI **0.164.0 app-server**，官方地址 `https://chatgpt.com/backend-api/codex`，现有ChatGPT登录，API-key环境移除。客户端/native二进制SHA见[client pins](preflight/client_pins.json)；真实请求、图片、提示、Schema和配置摘要见 runtime/transport。没有mock替代或补模型字段。
- 请求模型和CLI解析别名均为 `gpt-6.1-sol`；**服务端生成模型ID/快照未暴露，实际模型执行未建立**。别名解析通过不能写成实际快照已认证，也不能凭本次Schema错误证明该模型现已具备生成权益。

为满足不重试，使用独立受控transport，显式request/stream retries=0、禁WebSocket/模型fallback/工具，检测恢复或换模即停止。配置依据为[官方Codex配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)，并做了本地严格配置和零推理metadata握手；这些探针没有发送turn/start，不占生成预算，也不冒称验证了生成服务。

普通sandbox因CLI账号状态只读的探针失败未冒称网络或服务故障；实际运行在已授权的正常账号权限下，用现成17891代理及任务私有sqlite/log目录，无SSH转发。早期临时CODEX_HOME探针的不合规事实另在[本地探针记录](preflight/local_probe_incident.json)保留；最终代码未重设HOME/CODEX_HOME，也未抽取或打印认证令牌。

## 必要软件对接修复与离线证据

冻结前发现独立接受器把T1新增的确定性metric_contract_trace当作模型来源篡改。最小修复只在exp2分开原始模型来源与T1追溯，随后仍完整重建T1场景、T2、独立T4和执行积分。没有改Schema、提示词、车身、安全间距或模型输出。

[同一人工场景的基线/修复对照](preflight/acceptor_trace_regression.json)保留旧源码SHA：T4通过时，原接受器PROVENANCE_MISMATCH；修复后仅人工软件接受通过，online标志仍false。原接受器原字节另存，不回写旧实验。

[295项离线回归](preflight/verification/unittest.txt)全部通过、0跳过，27.160s；[来源](preflight/verification/provenance.json)记录源码和Python网络连接禁止。新transport协议/预算/无补值诊断/G2 gate/来源防篡改都经离线反例验证。**这些不是本次真实模型成功分子；本次也未实际到达接受器，不能宣称修复的在线效果。**

## 逐层结果与调用预算

[完整阶段台账](runtime/attempt_01/stage_ledger.json)、[运行汇总](runtime/summary.json)、[追加式预算账本](runtime/budget/final.json)保存实际状态。

| 环节 | 本次实际情况 |
|---|---|
| 请求 | 1次turn/start；初答尝试已扣预算。失败也计入，无自动请求/流重试 |
| 服务/通道 | 返回invalid_json_schema错误通知，willRetry=false；原生生成是否执行未建立 |
| 原生结构化输出 | NOT_REACHED，没有模型最终文本或JSON |
| T1与全部模型字段诊断 | NOT_REACHED，无模型场景字段可诊断；没有把历史422谓词套作本次失败 |
| T2 / T3 / T4 | 全部NOT_REACHED，实际工具调用0 |
| 独立接受器 | NOT_REACHED，不记通过 |
| 模拟前缀/积分/回执 | NOT_REACHED，没有轨迹、消费或执行回执；不能标MOVING_PREFIX或STATIONARY_HOLD |
| 语义修订 | NOT_ATTEMPTED，因服务接口失败停止；不算额外失败响应 |

原始台账的model_service_returned=UNKNOWN保持不变；本报告说“收到错误通知”指可观察的官方通道错误，不把它改写成模型生成成功。HTTP状态和生成快照未暴露。

[原始错误记录](runtime/attempt_01/failure.json)保存完整接口错误字符串；[净化RPC事件](runtime/transport/attempt_01/events.redacted.jsonl)、[请求](runtime/transport/attempt_01/requests.json)、[图片及提示/Schema清单](runtime/transport/attempt_01/input_manifest.json)、[wire摘要](runtime/transport/attempt_01/wire_manifest.json)保留输入输出链。本次没有初答/修订模型输出可交付，不以错误对象或人工JSON冒充模型输出。

预算限额2、已扣1、未用1；客户端turn/start=1、有效原生响应=0、修订=0。服务失败已结束本trial，禁止以同配置另开目录重新开始预算。模型端实际计算/重采样不可见，不编造其内部调用计数。

## 独立G2

[预注册](evaluation_preregister/preregistration.json)在新调用前已入Git，明确身体box中心XY、绝对地面速度、原采集参考时刻、1.2m偏移和共同域[-5,-12]–[45,12]m。原焦点不换，旧历史知情/非盲关联如实披露，新关联流程只看去数值语义视图及实际引用图像，关联冻结后才能看误差。

G2定义和独立GT预处理在调用前冻结；运行时从未读取GT；本次独立评价在runtime封存后进行，不称首次接触GT或全盲。由于没有原生输出，没有新actor关联，也没有任何模型米制数值可评分。

[独立评价报告](evaluation/report.json)、[说明](evaluation/README.md)、[输入完整性](evaluation/integrity_checks.json)保留：

- 1唯一窗口、1已尝试但无输出槽、第二槽未尝试；固定焦点1/1未评。
- actor级缺失/未匹配率没有声明对象分母，均为null；不能说检测全部漏检或缺失率为0。
- 位置、速度、道路、未来覆盖和膨胀全部null；规划可用性NOT_REACHED，无G2通过。
- GT注释中心插值是对照，注释位移斜率不能当瞬时速度真值；宽BODY surrogate包络重叠不能当可靠预测。上述边界即使将来有原生输出也保留。

G2核对20份公有runtime文件及5份预注册Git blob，未修改输入。25份私有状态/原始流没有发布，不能声称它们包含在公有runtime封存集合；[现场私有证据指纹](analysis/private_evidence_inventory.json)只记录路径/SHA/大小/权限，不发布内容。包含认证或账户信息的原始wire仅在现场0700/0600私有目录保存，不提交Git。

## 原因、六窗条件与后续边界

[机器结论](analysis/experiment_result.json)：**当前不具备直接展开六窗的条件，阻塞在结构化输出Schema接口兼容。** 本轮没有测试到模型所需米制/未来信息是否缺失；也未证明真实模型可生成该原生协议。

[离线Schema审计](analysis/schema_interface_audit.json)确认该文件是合法Draft7，但服务明确要求当前coordinate_frame节点的显式type；另有12处const-only节点。只定位已观察到的接口限制，不假定加完type就保证其余节点兼容。

[12处类型注记候选](analysis/proposed_wire_type_annotations.json)仅为离线提案，未应用、未发送、未改原Schema/配置。本轮不能把这类接口修复当“语义修订”再用剩余一次调用。

[后续建议](analysis/followup_recommendation.json)保持launched=false：先离线审查服务支持的Schema子集、冻结显式版本/新SHA，再单独确认新的受控试验；仍用本窗口，不换窗挽救结果。本轮剩余许可不自动转移，不展开24调用。

后续双臂必须保留以下边界：

1. 旧exec/default-retry与本次app-server/custom-provider/no-retry不是单变量对照；提示文件SHA相同也不能忽略客户端base/developer包装及结构化输出路径差异。应冻结双方一致的接入、预算、身份/失败规则，并披露契约+提示的一包变化。
2. 旧2.x身体点/速度定义不足，不能与新原生语义的G2准确率直接排名；注释插值/速度proxy、道路域不对应和未来surrogate限制仍在。
3. 本pilot只有1个已暴露窗口；初答/修订不是两个独立样本。原四窗/探索两窗分开；同配置纳入时只引用本trial一次，不能重跑替换失败。修复Schema或其他科学配置后必须另组，不把本次错误响应重新计为原组成功。
4. CLI别名解析不等于服务快照认证，生成/模型信息充分性尚未实测。单窗即使将来通过，也只能报告条件式链路和模拟前缀，不是真车安全、多轮反事实或G2通过。

## 可复核与停止状态

[runtime封存](runtime/SHA256_RUNTIME_MANIFEST.json)和[独立评价封存](evaluation/SHA256_EVALUATION_MANIFEST.json)相互独立，输出目标均为新目录，禁止刷新。实际源码/配置、原图SHA、原生请求和错误均保留。当前实验ended=true，后续六窗launched=false，用户本轮授权已按停止条件结束。

离线复核可以读取这些封存文件；不要重跑真实launcher“重置”预算。若需重跑离线测试，必须换未存在、且不在封存目录内的路径：

```bash
cd /data2/hzy/MAP/agentic-driving
.venv/bin/python implementation/experiments/run_metric_offline_tests.py \
  --out /tmp/new_pilot_offline_tests_20261028_C
```
