# Structured Outputs wire Schema：最小兼容修复离线交付

**新增真实模型调用 0；未使用旧 pilot 剩余许可，未启动新 trial 或六窗批次。** 完成独立 wire 层、三态兼容审查及实际离线验证。离线审查完成不等于服务已接受，也不构成在线成功率提高的证据。

最终源码冻结提交 **`e61de7a5140301691a14fc8cb50051c2f5b71628`**，历史基线 `b2ad856a1aa4c814dd38770445a0495ade1e0e7b`。全套验证使用同一冻结源码，[来源](verification/provenance.json)逐文件记录 SHA；产生本目录结果时的 worktree dirty 仅表示新增交付文件，源码已逐项与提交 blob 比对。

## 最小变换及业务边界

| 项目 | 固定内容 |
|---|---|
| 业务输出契约 | `3.0.0-exp2`，原 Schema 字节不变 |
| 提示词 | `driving_v3_exp2`，原字节不变 |
| 覆盖剖面 | `whole_interval_sources_v3_exp2`，原含义不变 |
| wire 版本 / 生成器版本 | `so_const_type_v1` / `1.0.0` |
| 业务 Schema SHA | `ae599d4744b9fe37ced1fd96cd883646daf6d0057dbc749cb805ed8a0819a6bd` |
| wire Schema SHA | `e11df43e3dadecf83f07cf7c2b000c12c843913ea5096bf05b0fc33968f223fe` |

[独立 wire Schema](offline/wire_bundle/wire_schema.json)和[逐项转换清单](offline/wire_bundle/transformation_manifest.json)保留原 SHA、wire SHA、版本、序列化方式、JSON pointer、原节点、新节点与等价依据。**仅新增十二处 `type: "string"`，const 原值及其余约束不变。** 实际转换类别只有 string const；其他类型在独立人工反例中验证，不能写成本业务发生了额外转换。

理由是 const 等值约束已蕴含所加类型，附加该类型不改变指定 Draft 7 语义下的合法实例集合。生成器仅遍历真正 Schema 节点，const/enum/default/examples 内的数据不被递归修改；anyOf 汇总节点、引用节点不猜类型。枚举未修改，required、null、物理数值与未知表达均未修改。

对于外部引用、引用普通数据的双重角色、不支持 dialect、嵌套资源 ID、重复键及不能数学保值往返的数字，生成器返回冲突并停止，不产出部分 wire。它不是任意 Schema 的服务兼容编译器。已有相互矛盾的 type/const 不被“修好”。[中文说明与迁移边界](../../docs/Structured_Outputs_wire_Schema_兼容层.md)明确支持范围及局部等价论证，不将样例差分当一般等价证明。

## 官方文档与固定客户端审查

[机器审查](offline/compatibility_review.json)逐 Schema 节点记录根对象、const/enum 类型、闭对象、required/可空、组合、引用/递归及结构限制。判定依据为实际查阅的[官方 Structured Outputs 指南](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)，文档审查日期 2026-10-28。

| 被审查对象 | 文档形式已确认支持 | 明确冲突 | 当前无法确认 |
|---|---:|---:|---:|
| 原业务 Schema | 312 | 1 | 66 |
| 新 wire Schema | 324 | 0 | 67 |

这是逐谓词检查条数，存在重叠，不是独立功能数、通过率或服务接受率。原业务的唯一“明确冲突”是旧 pilot 实际点名的 coordinate_frame 缺 type。其他同形节点没有分别探测服务。新 wire 消除了该已观察形状问题，但 typed const 的实际后端形式仍未实测。

新 wire 保留的67条未确认项为：12处 const 具体形式、8处 enum-only、41处 minLength、3处元注记/方言形式，以及深度计数、固定 CLI 后端支持、账户/模型部署各1项。没有删约束以获得“全绿”。实际为21个闭对象、121个属性、24个枚举值、跟踪字符串1892字符，语法 object/array 路径深度10；深度算法与服务计数是否相同仍未确认。本业务没有引用/递归，相关能力另外以人工审查反例覆盖。

[固定 CLI 文件核验](offline/client_pins.json)与原 pin 一致，版本0.164.0；native 二进制 SHA 为 `b93f6811d3f653f0fb83be4050d857fb9abbb111dfa2ce160563327daff7f306`。本地版本读取不产生推理。app-server RPC 接受通用 JSON 参数不能证明后端接受任意 outputSchema；当前文档也不能认证该账户、模型别名、服务端快照或具体部署子集。

## 运行时最小接入与验证

传输层新增显式 `wire_schema_bundle` 选择，默认仍发送原业务 Schema。wire 加载时及每次决定前完整重生并核对原业务 SHA、wire 字节、生成器及转换清单；注册还固定清单的字节 SHA，改排版也会被 pin 拒绝。原生最终文本先原样保存，JSON 解析结果另存；随后仍经过**原业务 Schema**及原 T1–T4、独立接受器。禁止补字段、强转、替换 null、修改尺度或数值。

[完整334项测试](verification/unittest.txt) **全部通过、0跳过，30.176s**。其中新增39项：17项 Schema 语义差分、14项三态审查、8项 wire 传输与授权/pin 回归。Python socket connect/connect_ex/create_connection 被禁止；RPC 使用明确标注 `mock_transport` 的本地人工进程，不执行 Codex 推理命令。

反例包含字符串/布尔/数值/null、1与1.0、bool与number区分、枚举、局部与递归引用、嵌套常量数据、$ref同级语义、不保值数字拒绝、wire/manifest篡改、原生错误字段拒绝且原文保留、旧默认行为、零许可和旧预算继承拒绝。完整套件继续覆盖真实 CasADi/IPOPT 人工数值、独立T4、碰撞拒绝和来源防篡改；不计在线分子。

[实际字段差分报告](offline/actual_field_differential.json)对十二个真实转换位置各列一个完整人工正控和六个错误值，共84条业务/wire判定对照记录：72个逐字段负变体，12条正控记录复用同一已校验人工实例。删除清单中新增的type即可恢复原业务 JSON；所有负例两边均拒绝。这些记录不是独立观测窗口。

开发阶段曾发现新增审查测试依赖其他测试先加载路径，已修复为独立导入；独立复核另发现转换清单 SHA 尚未参与启动校验，已补充字节校验和反例。最终334项覆盖最终提交，不把早期检查冒称最终全通过。

## 原证据保护

[保护清单](baseline_protection.json)登记937份既有公开文件；[结束核对](delivery_protection.json)确认935份字节未变。仅2份运行源码增加显式 wire 选择及新注册保护，修改前字节另存[基线源码ZIP](baseline_runtime_sources.zip)，并与基线 Git blob/SHA核对。原业务 Schema、所有旧提示词、实验配置、响应、失败、接受器结论、冻结ZIP与manifest原样保留。

T1–T4、业务validate、独立接受器均未修改；预算类 CallBudget、服务失败停止判定、PilotProtocol及provider无重试配置的AST与基线相同。旧默认行为亦有实际本地RPC回归。[写保护实测](write_guard_verification.json)确认既有输出返回 OUTPUT_EXISTS、封存目录返回 SEALED_EVIDENCE，未覆盖文件。

[原 pilot 核对](offline/old_pilot_integrity.json)逐项复算其48份公有封存文件及seal：**ended=true、预算1/2、原生输出0、剩余1未使用。** 没有重跑原launcher，没有把原错误改写为模型信息缺失。保护审计限登记文件与公开证据；未将现场私有认证/raw内容发布或声称全文件系统监控。

## 同窗口新 trial 仅为准备

[新注册](offline/trial_registration.json)为 **`exp2_wire_day_trial_20261028_v2`**，`launched=false`、`execution_authorized=false`、授权0、实际调用0；新runtime目录尚不存在。[交付校验](delivery_validation.json)重新核对采集与10张原图SHA及配置。

- 仍为原四窗白天窗口 **`ac452a60e8b34a7080c938c904b23057`**；历史已暴露、原ego速度0m/s，选择理由沿用预先固定记录，不换窗。
- 原车辆/1.2m偏移、安全间距、5s窗口、0.2s网格与前缀、模型别名、接入和无重试策略均保留。变量仅为传输Schema层，不修改提示或物理算法。
- 提议新独立预算上限2：初答1、必要时语义修订1，失败计预算；**目前批准额度仍为0，不继承旧未用1次**。接口探测如发送生成请求也占新预算，接口异常立即停止，不用修订槽修接口。
- 当前注册在任何输入验证、预算/transport创建或认证读取前被执行入口拒绝。未来若另行授权，须新建并冻结明确授权配置，不能覆盖本注册或旧pilot。
- 六窗批次继续launched=false；本trial若未来实际运行最多计一次，不能替换旧失败或将修订当独立窗口。接入/wire/业务/提示与计量语义的比较边界继续单列。

G2没有新增数值评价。原身体点、绝对速度、时刻、共同评价域及历史非盲暴露记录作为新注册参考保留；未来须在查看新数值前冻结新的关联与身份审阅流程，GT不进入运行时。当前没有新原生输出可用于G2或规划可用性评分。

## 离线复现

必须用尚不存在且不在封存证据内的新目录；以下命令不发送生成请求，不刷新本交付或原pilot：

```bash
cd /data2/hzy/MAP/agentic-driving
.venv/bin/python implementation/experiments/prepare_exp2_wire_trial.py \
  --out /tmp/new_wire_preparation_20261028_E
.venv/bin/python implementation/experiments/run_metric_offline_tests.py \
  --out /tmp/new_wire_regression_20261028_E
```

本轮停止于离线交付。尚未验证真实后端是否接受新wire；没有新增模型原生输出、在线链路成功、G2通过或真实车辆安全结论。[最终逐文件封存](DELIVERY_SHA256_MANIFEST.json)只覆盖本新目录，不改写任何历史manifest。
