# 计量语义与时空覆盖契约：最终离线交付

**新增真实模型调用 0，新增在线成功 0。** 最终输出契约为 **3.0.0-exp2**，提示词为 **driving_v3_exp2**，覆盖剖面为 **whole_interval_sources_v3_exp2**；分别记录，旧2.0/2.1默认行为保持。

最终源码冻结提交 **`15ff948c3d6acc58c7a2ca95e25aba2e8994e369`**。[实际汇总](offline/summary.json)、[源码与测试/Schema/提示词 SHA](offline/source_manifest.json) 对应本次最终验证。此前已封存的[完整技术报告](../metric_temporal_contract_v3/README.md) 保持原字节；本目录补齐通用运行日志写保护，并用同一最终源码重跑全部验证。不同阶段的重复回归不是新增独立观测窗口。

## 契约与实现

- [中文契约与迁移边界](../../docs/计量与时空覆盖契约3_0_exp2.md)、[新 Schema](../../schemas/agent_output_v3_exp2.schema.json)、[新提示词](../../prompts/driving_v3_exp2.txt)。目标点固定为身体 box 中心地面 XY，速度固定为绝对地面速度沿参考轴表达，不事后换定义。原1.2m仅用于既有 ego 后轴/中心及采集参考转换。
- [原生 T1 实现](../../agentic/metric_contract.py)：实际采集证据与状态时刻分开；异步必须有原生运动界、条件前提和残差；类型化未知绑定对象/空间/时段；不启用相关性裁剪。
- 粗整区间 BODY 源逐模式检查无正时间缺口，按闭边界保守细化，保留全部源/前提/残差/情景。总残差包含对齐与源几何之和，物理只膨胀一次。T4 从完整原输出 trace 重建核对，再独立检查动力学与碰撞。
- 明确尺度支持采用基座可证伪的条件身体尺寸先验；没有实际可见的尺度锚点仍拒绝。它不是可靠恢复单目绝对尺度的证明。新剖面要求原生位置和 BODY，可能增加拒绝。
- [严格 G2 与写保护规范](../../docs/G2_计量语义与证据写保护.md)：不可比定义/时刻/评价域不评分；未来覆盖、面积膨胀和规划证据分开，宽包络高覆盖不等于可靠预测。

旧协议/提示词/响应/配置/失败/接受器/冻结源码未重写。开发exp1的协议文件及源码另存，当前明确拒绝把exp1当exp2；也没有将历史2.x响应改标签后计成功。

## 最终验证

[全部246项测试](verification/unittest.txt) **通过、0跳过，20.173s**；包含原149项与本轮新增97项。[来源记录](verification/provenance.json) 保存最终源码SHA及 Python 网络 connect/connect_ex/create_connection 禁止状态。HTTP适配器只用进程内mock，source明确为mock_transport，不能计为真实模型。

| 人工集成案例 | 实际结果 |
|---|---|
| [6m/s移动](offline/authored_moving.json) | T1→T2→真实CasADi/IPOPT→独立T4通过；模拟执行0.2s，位移约1.14m、末速约5.40m/s |
| [静止等待](offline/authored_stationary.json) | 同链路通过，模拟静止短前缀 |
| [未来侵入静止自车](offline/authored_future_intrusion.json) | 不利分支保留，T2拒绝，独立T4为BODY_COLLISION，释放前缀0，无执行 |

全部是synthetic_fixture，无真实图像/模型/隐藏真值，不增加在线分子。新增反例还覆盖错误身体点/速度、假引用/陈旧/缺运动界/ego-only、粗包络边界、1e-12正缺口、null/占位/缺尺度/漏情景、前提空间/时间、残差预算及T4防篡改和版本降级。

通用RunLog也使用新目录独占创建和编号JSON独占写入；封存parent拒绝创建新Run，已有阶段文件不能覆盖。正常已有runs根下创建新的唯一Run仍兼容，日志编号与JSON格式不变。相关两项反例和原pipeline回归都纳入上述完整测试。

## 历史与G2差异

[27份逐响应/逐字段报告](offline/historical_predicate_comparison.json)：六个唯一窗口，原四窗/探索两窗及一次服务无响应分别保留；原422个失败谓词仍在，原首拒仍为MISSING_SCALE 11、UNBOUNDED_UNKNOWN 16，27份均拒绝。旧构造主体AST与基线相同，原值和guard依赖SHA一致；不把潜在错误算成额外历史运行。

[严格G2检查](offline/historical_g2_strict_refusals.json)：170条声明对象中位置有值51、缺失119；速度有值19、缺失151；既有非盲视觉关联51、未匹配119。旧计量语义不足，位置/速度评分均为0项，指标为null并给原因，绝不是零误差。旧假设下的探索分数和未来surrogate结果原样保留，不追写为模型准确率。

没有转换模型输出字段。[显式GT/审阅表格式桥接](offline/legacy_gt_identity_bridge.json)逐项记录401处身份字段别名及前提，仅保留既有instance/视觉证据，不补计量、数值、状态或覆盖保证。GT从未反馈运行时模型或工具。

[仍需模型及独立真值补齐的清单](model_information_requirements.json) 包含尺度依据、原生BODY/参考状态、运动对齐界、全部合理不利情景的完整未来包络、残差/前提/未知作用域，以及可比的瞬时速度与道路边界真值。旧147条occupancy=null不能靠格式修复消除。真实服务对新Schema的支持、原生输出稳定性及真实场景准确性、在线成功率均未验证。

## 保护与复现

[保护核对](delivery_protection.json)复算本轮开始登记的原文件，以及此前整份[已封存交付](../metric_temporal_contract_v3/DELIVERY_SHA256_MANIFEST.json)。12个获授权的入口/实现源码改动以原Git ZIP保留修改前字节，旧协议及实验数据不改；初次并行源码读值差异另有原记录，不冒称编辑前观察。[本目录SHA封存](DELIVERY_SHA256_MANIFEST.json)不覆盖任何历史manifest。

源码/文档归档遇到的中文路径问题及中间验证均在旧交付内原样保留，不混称模型或物理工具失败。封存后的交付不再追加；通用日志保护补充因此在本独立目录完成。

以下每个输出路径都必须尚不存在，也不能位于任何封存目录内。没有真实模型命令：

```bash
cd /data2/hzy/MAP/agentic-driving
.venv/bin/python implementation/experiments/run_metric_offline_tests.py \
  --out /tmp/new_metric_tests_20261028_B

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python implementation/experiments/metric_temporal_offline.py \
  --out /tmp/new_metric_replay_20261028_B

PYTHONPATH="$PWD/implementation" .venv/bin/python -m agentic.g2_metric \
  --records /path/to/read_only/native_record.json \
  --truth /path/to/read_only/metric_truth.json \
  --config /path/to/read_only/evaluation_domain.json \
  --out /tmp/new_metric_g2_20261028_B
```

新输出拒绝覆盖；源码自检要求与当前Git提交一致。G2最后一条是入口模板，本轮没有新增原生真实模型记录可供确认性评分。

[下一组实验提案](next_experiment_proposal.json)继续 **launched=false**：六个原唯一窗口、旧/新两臂最多24调用，一次语义修订、无自动传输重试、600s/调用、总wall18000s；原四窗/探索两窗分开，失败消耗预算，服务/身份/字节/GT运行时边界异常立即停止。它是契约与提示的一包变化，不称单因素因果对照；未经确认不启动，不预设成功率提高。
