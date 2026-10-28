# 真实模型输出的只读流程契约审计

**工程接口存在可修复问题；修复不等于米制 grounding 或未来覆盖已成立。** 本审计没有调用模型、四工具或读取 GT/地图。原数据不修改。

按归档响应逐份检查；重复观测和反馈修订不当作独立场景，原 2.0 与独立 2.1 格式复核不合并成同一协议成功率。超时请求没有输出，仍保留在原始实验失败账本，不从总体分母消失。

| 项目 | 实际数量 |
|---|---:|
| 有效图像响应 / 不同观测窗口 | 10 / 4 |
| 原 2.0 / 独立 2.1 响应 | 9 / 1 |
| 决策有效期短于 5s 规划时域 | 6 |
| 目标区域排除初始中心 | 3 |
| 按原 v2 T2 内缩后目标区域为空 | 10 |
| 当前剖面拒绝的 calibrated_ground_plane 标签 | 8 |
| 明确 scale=unknown | 2 |
| 目标条目（含重复） | 59 |
| 非空占用 / 网格错误 | 54 / 54 |
| 明确未知占用 / 完整网格占用 | 5 / 0 |
| 未知数值不确定范围 | 55 |
| unbounded_unknown=true 响应 | 10 |

## 可修复的流程问题

1. `target_region` 被输出成小停车框或仅前方的终点框；工具实际把它施加于整个时域，且按中心偏移保守内缩。新提示词必须解释全时域允许 CENTER 区域，而非要求模型凭空扩大可行道路。
2. 规划有效期与 0.2s 执行前缀混淆；提示词须明确复制采集元数据的规划有效截止，不意味着旧核验可跨新观测沿用。
3. 原非空 occupancy 全部只有一段；T1 要求每个网格整区间一段。必须提供明确网格要求，或诚实返回 null；不能把未获支持的占位矩形切成 25 段就当预测成功。
4. Schema 允许的 calibrated_ground_plane 标签不等于当前 T1 支持该投影契约；新提示词须明确禁止该标签。只有原本有依据的条件米制假设才可用 model_metric_hypothesis，不能靠换标签修复缺尺度。
5. 仅返回第一个 UNBOUNDED_UNKNOWN 会遮住同时存在的有效期、目标区和网格错误。可聚合纯契约诊断，仍不得修补环境事实或静默放松硬约束。

## 逐响应摘要

| 来源 | 协议 | 有效期(s) | 目标内缩空 | 尺度标签 | 占用段数 / 目标 |
|---|---|---:|---|---|---|
| `20261027T153851Z-7d14c064:0006-model-record.json` | 2.0.0 | 0.200 | True | calibrated_ground_plane | 1,1,1,1,1,1,1,1 |
| `20261027T153851Z-7d14c064:0009-agent.json` | 2.0.0 | 0.200 | True | calibrated_ground_plane | 1,1,1,1,1,1,1,1 |
| `20261027T153851Z-7d14c064:0012-agent.json` | 2.0.0 | 0.200 | True | calibrated_ground_plane | 1,1,1,1,1,1,1 |
| `20261027T153851Z-7d14c064:0015-agent.json` | 2.0.0 | 0.200 | True | calibrated_ground_plane | 1,1,1,1,1,1 |
| `20261027T154925Z-b9869784:0006-model-record.json` | 2.0.0 | 5.000 | True | calibrated_ground_plane | 1,1,1,1 |
| `20261027T154925Z-b9869784:0009-agent.json` | 2.0.0 | 5.000 | True | calibrated_ground_plane | 1,1,1,1,1 |
| `20261027T154925Z-b9869784:0012-agent.json` | 2.0.0 | 5.000 | True | calibrated_ground_plane | 1,1 |
| `20261027T154925Z-b9869784:0018-model-record.json` | 2.0.0 | 5.000 | True | calibrated_ground_plane | 1,1,1,1,1,1,1 |
| `20261027T154925Z-b9869784:0021-agent.json` | 2.0.0 | 0.200 | True | unknown | 1,1,1,1,1,1,1 |
| `20261027T161613Z-a58e5a49:0005-model-record.json` | 2.1.0 | 0.200 | True | unknown | null,null,null,null,null |

完整字段、原 confidence_note、来源 SHA256 见 [contract_audit.json](contract_audit.json)。`placeholder` 文本计数只是原文是否含该字面词，不是自动判定支持程度的分类器。占用可支持性、道路未知与未来侵入仍须独立面对；本审计不作通过结论。
