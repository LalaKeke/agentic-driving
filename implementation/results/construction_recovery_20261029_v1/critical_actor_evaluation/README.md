# 关键对象轻量离线对照

**新增模型调用0，GT从未进入运行时。** 固定停车场原真实输出attempt_02与施工原真实输出attempt_03；只选两窗各两个决定规划的对象，3/4可唯一视觉关联，1/4为聚合设施不可比；四项位置与占用均非null。不是检测召回率或数据集准确率。

| 窗口/对象 | 中心误差m | 长/宽误差m | 航向误差rad | 2秒占用覆盖 / 面积膨胀 |
|---|---:|---:|---:|---:|
| 停车场右侧Mercedes | 3.156 | 0.174 / 0.256 | 0.0722 | 0.00255 / 3.748倍 |
| 停车场左侧Nissan | 2.323 | 0.417 / 0.246 | 0.0700 | 0.41778 / 4.227倍 |
| 施工前方浅色MPV | 0.923 | 0.027 / 0.057 | 0.1558 | 1.00000 / 27.712倍 |
| 施工隔离设施整体 | 不评分 | 不评分 | 不评分 | 不评分：模型18m集合与单体注释不对应 |

表中覆盖与膨胀是[逐区间报告](oriented_evaluation/report.json)的**有向车身注释插值surrogate**：注释中心/尺寸线性、平面航向最短角插值，最大0.02秒子段的端点凸包加保守运动余量。模型全部情景联合，原`uncertainty_m`总残差只膨胀一次，不重复加源/对齐分量，也不加自车1米间距。十个0.2秒区间等权平均；它不是实际观测到的连续真值。原始、未膨胀包络的覆盖/膨胀也保留：Mercedes 0 / 1.649倍，Nissan 0 / 1.869倍，前车0.99857 / 18.160倍。

两个停车场近车名义中心均比注释参考中心偏后约2–3米；即使规划通过，这些条件场景仍可能不准确。前车包络非常宽，其高surrogate覆盖不能当可靠预测；静止/连续运动是否符合实际也不由该指标证明。另保留更保守的注释中心外接圆surrogate结果，两个定义不能混作同一指标；面积膨胀的分母改变时结果会变化。

当前计量采用3.0.0-exp2的身体box中心地面XY、参考自车坐标与既有1.2m后轴/中心偏移。注释中心在同一instance合法相邻时刻之间插值，间隔不超过0.75秒；尺寸、航向也只是注释对照，非精确车辆实测。停车场与施工两窗的前向图比sample注释时刻早约34/35ms，评分按实际图像参考时刻插值；不能直接用SDK keyframe返回的sample时刻注释冒充精确同刻。

[身份审阅](identity_review.json)只按外观、原引用相机及[官方SDK投影](review_images/projection_index.json)关联；右侧Mercedes为前景车辆label18（不是后排label12），左侧Nissan为label3，施工前车为label23（旧历史投影label114，同一instance）。[Mercedes单独投影](review_images/selected_CAM_FRONT_RIGHT_18.jpg)与[Nissan单独投影](review_images/selected_CAM_FRONT_LEFT_3.jpg)可检查。SDK对keyframe使用sample注释、对中间sweep插值，因此这些投影仅确认身份，不用于读取米制分数。

这是**已知模型数值和历史GT后的非盲事后助手匹配**，没有按误差最小选择身份，不追称独立盲测。施工barrier包含多块设施，缺少合法集合GT；没有以某个最近单体的尺寸/中心替代。规划可用性应看本轮独立T1–T4与执行记录；这些GT分数不改变规划场景，不构成G2通过或真实安全结论。

[算术与计量核对](arithmetic_checks.json)重算3项位置误差及30个区间的覆盖范围/膨胀单调性。最初圆盘surrogate报告留在`evaluation/`，最终同时含两种surrogate的报告为`oriented_evaluation/`，不混为额外窗口。报告保存实际源码SHA及输入SHA；源文件当时尚未提交，提交号仅表示基线，不冒称干净冻结源码。

复现只生成未存在的新目录，不调用模型：

```bash
cd /data2/hzy/MAP/agentic-driving
PYTHONPATH="$PWD/runs/tools/nuscenes-visualization/lib/python3.11/site-packages" \
  .venv/bin/python implementation/experiments/critical_actor_comparison.py \
  --review implementation/results/construction_recovery_20261029_v1/critical_actor_evaluation/identity_review.json \
  --out /tmp/new_critical_actor_comparison
```
