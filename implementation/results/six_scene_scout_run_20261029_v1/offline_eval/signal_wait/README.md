# 信号等待窗：未被T1接受的原生米制假设对照

**仅1个已封存窗口、1份有效原生输出attempt01，2个指定对象；新增模型调用0、GT反馈0。** 修订遇服务错误没有有效JSON，不补造第二响应。首答因queued_motion对left_black_sedan的PREMISE_SCOPE被T1拒绝，T2/T3/T4和前缀均NOT_REACHED。

|原生参考时刻假设对象|中心差m|长/宽差m|BODY yaw参数差rad|有向未来surrogate覆盖|面积膨胀|
|---|---:|---|---:|---:|---:|
|cross_silver|0.3952|0.114 / 0.179|0.08985|1.00000|13.85567倍|
|left_black_sedan|1.1868|0.446 / 0.039|0.02831|1.00000|2.97591倍|

2/2优先槽合法视觉匹配。横向银车对应实际CAM_FRONT官方投影label12；近侧黑Mercedes对应实际CAM_FRONT_LEFT label1，车身被近相机边界截断，身份可对应但不是全车尺寸均可直接看见。按可见外观和相机关系关联，没有按数值误差找GT；[身份先冻结](identity_freeze.json)，属于非盲事后对照。

**定义可比不代表T1已经认可对齐。** 原生3.0.0-exp2明确位置是BODY-box中心地面XY、固定ego参考轴及采集时刻1535489300.012404s；故可对模型自报的该参考状态与同instance合法注释插值作差。原前提未覆盖所需过去时间的T1拒绝完整保留，不能通过这份评价延长前提、补齐运动信息或称时间对齐已成立。主统计列为“原生假设对照”，而非已通过工具的状态精度。

位置/BODY/占用两槽均有值，当前定义可比2、T1已接受的对齐0、未来注释surrogate完整10区间2。所有合理不利情景和原总残差一次保留，不加自车1m安全间距作预测评价。未来覆盖来自原录像中注释插值身体的观测surrogate，不是真实连续真值或新轨迹执行后的反事实响应。高覆盖和宽包络不能替代规划通过、校准保证或真实车辆安全。

[详细原生假设/GT代理数值](native_hypothesis_evaluation/report.json)、[机器汇总及原拒绝](summary.json)、[前向官方投影](identity_projection/signal_wait_CAM_FRONT.jpg)、[左前官方投影](identity_projection/signal_wait_CAM_FRONT_LEFT.jpg)。两个位置范数及20个未来区间范围/膨胀单调性复核通过。未读后3个未调用窗的输出或GT，不改运行时或原响应。
