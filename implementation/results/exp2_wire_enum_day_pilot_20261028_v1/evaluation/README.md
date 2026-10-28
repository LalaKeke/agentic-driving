# 同窗口新 wire 小试：独立离线 G2

**只有1个历史已暴露窗口、2份重复观测响应；没有在线链路通过或 G2 通过结论。** 本评价新增模型调用0，GT及关联未反馈给运行时。

两次真实请求均形成原生业务 Schema 合法JSON，均在T1拒绝。T2、T3、T4、独立接受器和模拟短前缀消费均为 NOT_REACHED；不存在可评估的移动或静止执行轨迹。G2不由位置差值或占用重叠推断规划可用性。

## 先冻结身份，再读取数值

预注册沿用原身体box中心地面XY、绝对地面速度沿固定参考轴、原采集参考时刻、1.2m自车参考偏移及共同评价域[-5,-12]–[45,12]m。原窗口、旧GT和旧非盲关联已知情，不追称全盲或未见测试。

先仅查看两份固定词汇无数值semantic view、模型实际引用的CAM_FRONT原图，以及官方SDK在同一图像的注释投影。每轮四个声明中，红色衣着/前景横穿线索唯一对应同帧label31（固定焦点）；另外三个只有白色或泛化前方横穿线索，存在多个合理候选，全部保留歧义未匹配。没有看模型坐标/速度/BODY/占用，也没有按最小误差、最近目标或排除法强配。

[身份冻结清单](identity_review/SHA256_IDENTITY_MANIFEST.json) SHA为 `55d4b4b3608e5f555253cb4de27957547c72c033f5b9271994dbd2f6e87437b7`。身份冻结通知发出后才读取本轮米制值；root也报告在收到该通知后才查看本轮指标字段。[时序披露](review_timing_disclosure.json)记录该协作事实，不将其冒称重建的网络时间戳。

## 完整分母与当前量

| 项目 | 初答 | 语义修订 | 合计及含义 |
|---|---:|---:|---|
| 声明对象行 | 4 | 4 | 8行、相同4个声明ID，不是8个独立真实目标 |
| 位置有值 / 缺失 | 4 / 0 | 4 / 0 | 8/8有值；声明行缺失率0%，不是检测召回率 |
| 速度有值 / 缺失 | 0 / 4 | 0 / 4 | 缺失率100% |
| 唯一身份关联 / 歧义未匹配 | 1 / 3 | 1 / 3 | 未匹配6/8（75%） |
| 位置可评 | 1 | 1 | 同一个焦点GT身份重复2次，另6/8不可比 |
| 焦点位置XY差值 | 0.427700645m | 0.427700645m | 不是数据集精度或独立稳定性实验 |
| 道路输出 | null | null | 两槽缺失；道路边界准确性不评分 |

位置参照是约2Hz注释身体box中心在采集时刻的线性插值，不是精确测得的瞬时真值。两次相同数值来自初答及语义反馈修订，不是两个独立窗口、两个独立身份或专门的重复调用稳定性实验。严格计量/身份检查及所有未匹配项保留在[完整机器结果](metrics.json)、[逐行CSV](object_rows.csv)及[分槽汇总](summary.json)。

速度本轮全部null；独立GT的速度又只是注释区间位移斜率，按冻结规则不能替代瞬时速度真值。道路本轮本就缺失；即使未来输出局部road patch，未建立对应的实际道路边界定义时也不能用全图drivable extent替代评分。

## 未来覆盖、膨胀与规划可用性

未来occupancy共8行均为null，缺失率100%。两个已唯一关联行报告 MODEL_OCCUPANCY_MISSING；六个歧义行先被身份可比性门槛拒绝，但其occupancy缺失仍完整计入分母。因此未来覆盖和面积膨胀均为 **null、可评数0**，不是零覆盖，也不是高覆盖。

原GT未来BODY仅为注释线性运动加身体外接界的surrogate，不是真实连续占用。若有模型包络也必须同时报告覆盖、膨胀和未覆盖域，不能把宽包络的高重叠解释成可靠预测。本轮实际没有可评分包络，规划可用性由封存T1拒绝确定，后续均未到达。

## 完整性与复算

独立核对了runtime公有封存的全部48项，以及预注册5份文件在实际运行提交 `23280fcce39b928760ebd91763bae940a6acb8f1` 的Git blob。30份明确私有state/raw文件只列排除路径、不读取内容；不宣称整个runtime的私有内容均纳入公有封存。[完整核对](integrity_checks.json)及[来源和输入SHA](provenance.json)可复算。

模型输出未转换。`derived_truth_with_frozen_associations.json` 是独立GT的派生副本，仅加入已冻结身份关联；原GT、图像、原生输出、失败和预算原字节均未改。分析源码在本目录保存，属于运行结束后的离线分析，不冒称参与了调用前冻结的runtime源码。首次离线脚本使用了旧版注册文件名，发生FileNotFoundError并在产生数值结果前退出；[incident](analysis_preparation_incident.json)及初始源码保留，修正为实际registration/authorized_config.json后才完成本评价。该报告路径问题不算模型或物理工具失败。

以下仅重新计算严格G2数值，输出必须是尚不存在、且不在封存目录内的新路径；不要覆盖本交付、旧G2或runtime：

```bash
cd /data2/hzy/MAP/agentic-driving
PYTHONPATH="$PWD/implementation" .venv/bin/python -m agentic.g2_metric \
  --records \
    implementation/results/exp2_wire_enum_day_pilot_20261028_v1/runtime/attempt_01/record.json \
    implementation/results/exp2_wire_enum_day_pilot_20261028_v1/runtime/attempt_02/record.json \
  --truth implementation/results/exp2_wire_enum_day_pilot_20261028_v1/evaluation/derived_truth_with_frozen_associations.json \
  --config implementation/results/exp2_wire_enum_day_pilot_20261028_v1/evaluation/evaluation_config.json \
  --out /tmp/new_enum_wire_g2_recalculation_20261028_A
```

接口接受及JSON生成已经观察到，原生驾驶场景仍缺道路、速度和未来包络等必要信息；该结论不把Schema兼容误称为真实驾驶安全。新独立预算已用2/2，trial已停止，六窗批次没有启动。
