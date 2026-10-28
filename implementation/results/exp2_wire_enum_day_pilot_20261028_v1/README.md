# enum 类型检查与同窗口受控小试

**本轮已结束并停止：新独立预算2/2，两次真实请求均形成通过原业务Schema的原生JSON；两轮均被T1拒绝，没有轨迹或模拟前缀消费。** 这两次没有重现此前的missing-type接口错误，但不能将原生JSON生成解释成完整驾驶链路通过。

源码冻结 **`06db69ba4201b1e0363d467fb09740b24bbb4bc7`**；实际配置、wire及G2预注册于 **`23280fcce39b928760ebd91763bae940a6acb8f1`** 提交后启动。[逐层结果](layered_results.json)、[原运行汇总](runtime/summary.json)和[预算账本](runtime/budget/final.json)保留初答与一次语义修订。运行wall约165.362s，退出码3表示没有完整链路成功，不把正常T1拒绝写成服务错误。

## 实际8处枚举与新wire

以下8处enum-only的24个值全部是字符串；仅添加冗余 `type:string`，枚举值及其他约束不变。

| 实际字段 | 原枚举值 |
|---|---|
| decision.behavior | stop_yield, hold_lane, resume |
| decision.hard_requirements/items | target_region, stop, yield, vehicle_limits, collision_free, road |
| hypotheses.scale_basis | model_metric_hypothesis, calibrated_ground_plane, unknown |
| objects/occupancy/anyOf/0/items/status | supported, placeholder |
| objects/alignment/method | direct_reference_observation, model_motion_bound |
| objects/scenarios/items/role | adopted, reasonable_adverse |
| unknowns/items/kind | not_provided, not_expressible, multiple_conditionals, no_finite_bound |
| metric_scale_support/anyOf/0/anchors/items/dimension | length_m, width_m |

完整JSON pointer、原节点、新节点、值类型和等价依据见[转换清单](registration/wire_bundle/transformation_manifest.json)。新[wire Schema](registration/wire_bundle/wire_schema.json)版本 **`so_const_string_enum_type_v2`**，生成器 **`2.0.0`**，SHA **`c2e95df4406b292918a6bb85baa4a31b88467abeac613a7daa9e191eb021efbb`**；共20处注记：继承原12处const，加8处字符串enum。

字符串枚举的成员资格已蕴含string类型，新增交集约束不改变指定Draft7语义下的合法实例集合。生成器只遍历真正Schema节点；不修改const/enum/default/examples内部数据，不从引用或联合汇总猜类型。遇到非字符串enum-only会逐值列冲突并停止，不强转或猜联合。针对性正反差分不冒称一般Schema等价证明。

旧v1生成器、wire和封存报告保持原字节，版本分派明确区分v1/v2，无静默降级。业务契约 **3.0.0-exp2**、提示词 **driving_v3_exp2**、覆盖剖面 **whole_interval_sources_v3_exp2** 不变；原业务Schema SHA仍为 `ae599d4744b9fe37ced1fd96cd883646daf6d0057dbc749cb805ed8a0819a6bd`。

[调用前三态审查](registration/compatibility_review.json)为340条文档形式支持、0条明确冲突、59条未确认检查，存在重叠，不是服务接受率。未确认包括41处minLength、12处const具体后端形式、元注记、深度计数及部署能力；没有为消除它们删除约束。[官方Structured Outputs指南](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)仅作为文档范围依据；本次真实证据也只限两个固定请求，不认证所有Schema、账户能力或模型快照。

## 冻结、真实接入与预算

同一原四窗白天样本 **`ac452a60e8b34a7080c938c904b23057`**，历史已暴露，原ego速度0m/s。[授权配置](registration/authorized_config.json)固定同一10张原图SHA、采集SHA、原车辆/1.2m偏移、安全间距、5s规划、0.2s网格和前缀；不换窗，不改物理标准。

真实客户端为固定官方Codex CLI **0.164.0 app-server**，地址 `https://chatgpt.com/backend-api/codex`，使用既有ChatGPT登录。API-key环境被移除，正常进程代理统一为既有17891端口，无SSH转发。模型请求及CLI解析别名均为 **gpt-6.1-sol**；**实际服务端生成ID/快照未暴露，继续标UNKNOWN**，HTTP状态也未暴露。

初答与语义修订各一次turn/start，各扣一次预算，剩余0；失败照常计预算。没有额外生成型兼容探测、请求/流重试、WebSocket、模型fallback、工具调用或六窗批次。客户端可以观察两次turn/start，不能据此编造服务端内部计算或重采样次数。

原pilot仍ended=true、预算1/2、原生输出0、剩余1未使用。本trial没有继承原剩余额度。配置中的launched=false及wire manifest中的NOT_TESTED/0描述的是提交冻结时刻；实际已启动、2/2消费及结束事实分别由独立runtime与launcher记录，不回写预注册。

## 初答与修订逐层结果

| 环节 | 初答 | 一次语义修订 |
|---|---|---|
| 服务通知 | 完整turn完成，无error通知 | 完整turn完成，无error通知 |
| 原生输出 | 有最终文本和JSON | 有最终文本和JSON |
| 原业务Schema校验 | PASS | PASS |
| T1 | FAIL / UNBOUNDED_UNKNOWN | FAIL / UNBOUNDED_UNKNOWN |
| T2 | NOT_REACHED | NOT_REACHED |
| T3 | NOT_REACHED | NOT_REACHED |
| T4 | NOT_REACHED | NOT_REACHED |
| 独立接受器 | NOT_REACHED | NOT_REACHED |
| 模拟前缀回执、积分 | NOT_REACHED | NOT_REACHED |

原始文本：[初答](runtime/transport/attempt_01/final_agent_message.txt) / [修订](runtime/transport/attempt_02/final_agent_message.txt)。原生JSON：[初答](runtime/transport/attempt_01/native_output.json) / [修订](runtime/transport/attempt_02/native_output.json)。分层台账：[初答](runtime/attempt_01/stage_ledger.json) / [修订](runtime/attempt_02/stage_ledger.json)。原生文本原样保存后仍通过原业务校验，未补字段、强转、替换null或修改尺度/模型数值。

两轮实际首拒均为“Unknown road/extent blocks planning”：`/hypotheses/road=null`、`/hypotheses/unbounded_unknown=true`。每轮4个目标的未来occupancy全部null；无法证明五秒完整覆盖。没有用速度外推另造占用、缩小未知或改为静止安全结论。

速度本轮也全部缺失，但null速度单独不是T1拒绝条件；有受支持完整占用包络时不能仅凭缺瞬时速度拒绝。本次道路和未来覆盖缺失已足以阻塞，不能把所有缺失量一概写成独立必要条件。

[初答全部诊断](runtime/attempt_01/all_contract_diagnostics.json)有336条检查、25条失败谓词；[修订全部诊断](runtime/attempt_02/all_contract_diagnostics.json)有334条检查、19条失败谓词。每条保留原值、指针、证据和源码guard；它们是同一原输出的独立纯诊断，不能都称为实际执行过的T1守卫，也不是多次历史运行。

| 失败谓词 | 初答 | 修订 |
|---|---:|---:|
| UNBOUNDED_UNKNOWN | 2 | 2 |
| TYPED_UNKNOWN_BLOCKING | 5 | 5 |
| CONDITIONAL_SCOPE | 4 | 4 |
| SCENARIO_INCOMPLETE | 4 | 4 |
| OCCUPANCY_UNKNOWN | 4 | 4 |
| UNSUPPORTED_TRAFFIC_RULE | 3 | 0 |
| UNKNOWN_TRAFFIC_CONDITION | 3 | 0 |

字段谓词可重叠，数量减少不能证明环境事实正确或安全改善。有限身体尺寸先验、米制估计、残差和未来假设仍是可证伪的模型条件，不能当已知真实尺度或校准保证。本次阻塞已到模型输出未满足当前场景覆盖契约，而非此前missing-type接口错误；不据此断言模型原则上无法补齐必要信息，也不预设T1应放宽。

没有轨迹、前缀回执或积分结果，不能标MOVING_PREFIX或STATIONARY_HOLD，更不能将原ego静止当安全通过。

## 独立G2

[调用前预注册](evaluation_preregister/preregistration.json)保持原身体box中心地面XY、绝对地面速度参考轴、采集参考时刻、1.2m偏移、共同域[-5,-12]–[45,12]m和固定焦点。历史窗口、GT及旧非盲关联已暴露，不追称全盲。新关联只用无模型数值的语义视图、实际引用图像和官方投影；[身份冻结](evaluation/identity_review/SHA256_IDENTITY_MANIFEST.json)后才解封本轮数值。GT、关联与投影从未反馈模型或T1–T4。

两轮各4声明，共8对象行，来自**1个唯一窗口**；初答与语义修订不作为独立重复稳定性实验。[独立评价报告](evaluation/report.json)、[全部对象](evaluation/object_rows.csv)及[评价说明](evaluation/README.md)保留缺失、不可比和未匹配。

- 位置8/8有值；仅同一焦点GT身份在两轮中的2条记录可唯一关联评分，6/8歧义未匹配（75%）。两次位置差值均约 **0.427701m**，对照为nuScenes注释中心插值，不是精确瞬时真值，不作数据集准确率或召回结论。
- 速度0/8有值，缺失100%；而GT注释位移斜率也不能当瞬时速度准确率真值。道路2/2为null，未评分；原道路评价域对应限制保留。
- 未来占用0/8有值，缺失100%。覆盖及膨胀均为null，不能解释成零覆盖/零膨胀或可靠预测；规划可用性为T1拒绝、无可用前缀。

没有设通过阈值，没有G2 PASS。相同焦点位置的两次重复差值也不能抵消速度、道路和未来包络缺失。

## 验证、保护与停止

[345项离线回归](offline_verification/unittest.txt) **全部通过、0跳过，31.349s**；其中本轮新增10项有限enum差分和1项显式v2本地RPC回归。Python网络连接禁止，本地RPC source明确mock_transport；测试不计真实成功。

[调用前保护](pretrial_verification.json)及[独立后审](analysis/independent_transport_review.json)核对1002份登记文件：1000份原字节不变，仅两份运行源码更换loader分派导入，非import AST完全相同，旧字节另存[源码ZIP](baseline_runtime_sources.zip)。业务Schema、旧协议/提示词/配置/响应/失败/接受器/预算/封存源码、T1–T4、原wire v1均不改。审计限登记范围，不是整盘监控。

[runtime封存](runtime/SHA256_RUNTIME_MANIFEST.json)核验48份公有文件；30份现场私有state/raw不在公有封存集合内，仅以现场指纹和权限核对，不发布内容。私有目录0700、raw0600，均被Git排除。不能把公有封存称为所有私有文件都已发布。

不要重新运行真实launcher或用新目录重置已消费预算。离线回归如需复现，必须使用全新且非封存目录，不调用模型：

```bash
cd /data2/hzy/MAP/agentic-driving
.venv/bin/python implementation/experiments/run_metric_offline_tests.py \
  --out /tmp/new_enum_pilot_offline_tests_20261028_F
```

本轮没有完整条件式单窗口通过、真实车辆安全、多轮反事实闭环或成功率提升结论。**两次预算已消费并停止，不修改Schema后继续请求，不启动六窗批次。** [本交付最终SHA清单](DELIVERY_SHA256_MANIFEST.json)仅封存此新目录，不改任何旧manifest。
