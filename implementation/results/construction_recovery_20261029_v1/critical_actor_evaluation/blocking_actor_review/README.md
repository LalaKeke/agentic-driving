# 原施工输出的两个实际阻塞对象

**新增真实调用0；只读原attempt_03，不读本轮新在线输出，不反馈GT运行时。** 两对象均能按模型原文与实际引用图像唯一关联；2选定/2匹配、位置与占用缺失0，来自1个已暴露窗口。它是事后非盲对照，不是独立测试集。

| 原对象/语义证据 | 合法身份 | 中心误差m | 长/宽差m | 原包络覆盖/膨胀 | 加原总残差后覆盖/膨胀 |
|---|---|---:|---:|---:|---:|
| `left_curb_pedestrian`；`left_pedestrian_motion`明确“striped-shirt phone-holding pedestrian” | 前左图SDK label140，`eb3da8843f614a90a48ddb6ee392c0c0`；不是左侧白衫label144 | 0.21490 | 0.061 / 0.010 | 1.000 / 26.706倍 | 1.000 / 72.734倍 |
| `right_worker`；`worker_motion`明确“Nearest yellow-helmet worker” | 前图SDK label56，`71603dd1a2ba4e9daf095535e38310ac`；近处背向相机黄盔、反光背心、黄靴 | 2.43332 | 0.137 / 0.075 | 1.000 / 26.307倍 | 1.000 / 67.510倍 |

[逐字段身份依据](identity_review.json)保留完整原premise、原引用帧与图像SHA、实例/注释token、官方投影路径及标签。没有按位置误差选择身份。前左原图引用`b558073505fa411798d4dc174e1bdaea`；前图引用`0b2eb9c1cd1b45f0b1664a01684c5767`。[右工人原图裁剪](right_worker_original_crop.png)仅方便离线身份审阅，裁剪与2倍展示缩放关系在[crop_and_sources.json](crop_and_sources.json)，未供模型或工具使用。

上述覆盖/膨胀来自[逐区间机器结果](evaluation/report.json)的有向车身注释插值surrogate，与前两窗同一定义。模型全部合理不利情景、闭边界相接源的保守并集保留；只加原`uncertainty_m=1.3m`总残差一次，不再加内部对齐/源残差，不加自车1米间距。原始未膨胀包络、外接圆surrogate及全部20个区间也保留。注释中心/尺寸/平面yaw插值和保守子段只是评价代理，不是实测连续真值。

两对象原生都是0.7m方形BODY、yaw=0，模型明确说该角度用于box近似。机器报告字面BODY角差为2.77766rad、0.35753rad；**不能将其当行人实际面朝方向误差**，方形几何也不唯一确定heading。本对照对“行人朝向准确率”不评分，保留字面值供几何核对。

可以确认左侧行人当前中心并没有明显数米偏移，而未来不利包络相对该注释轨迹代理很宽；右工人中心有约2.43m差值。二者仍不能证明模型不利未来不合理，更不能用评价GT删情景、移动障碍或缩小残差求通过。规划器改进与当前模型条件场景是否可行，仍应由独立工具链比较判断。

```bash
PYTHONPATH="$PWD/runs/tools/nuscenes-visualization/lib/python3.11/site-packages" \
  .venv/bin/python implementation/experiments/blocking_actor_comparison.py \
  --review implementation/results/construction_recovery_20261029_v1/critical_actor_evaluation/blocking_actor_review/identity_review.json \
  --out /tmp/new_blocking_actor_comparison
```

在项目根目录执行，输出路径必须不存在；该命令不调用模型。
