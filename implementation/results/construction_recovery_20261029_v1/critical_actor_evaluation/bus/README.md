# 公交关键对象：身份/身体粒度不可比

**新增真实调用0。** 固定原公交成功窗`7ff999a1242e4573a81dd3a6f0c158a2`的真实attempt_02，选择唯一关键对象`curbside_bus`，原生位置和占用均有值；1个唯一窗口、1个焦点、0个可比匹配、1个未评分。原在线条件式T1–T4与模拟移动前缀成功仍独立成立，不能借其规划通过宣称米制准确。

[官方SDK投影](review_images/moving_prototype_2s_20261028_v1_CAM_FRONT.jpg)中，相邻同向公交有两个`vehicle.bus.bendy`框，label11/19，对应instance `df8a0ce6d79446369952166553ede088`与`16895bcf420a4a9eb96d1b125ba69a98`。[数据集原始类别说明](annotation_category_evidence.json)明确：“Annotate each section of the bendy bus individually.” 两段注释长度分别9.48m、8.78m，原模型却为单个12m BODY。数据中没有在本审阅流程中建立的整车分段归组及对应完整BODY定义。

因此不能挑较近或误差较小的一段充当模型整车，也不静默将两段合并成新GT。当前[身份审阅](identity_review.json)固定为`BODY_GRANULARITY_AMBIGUOUS_NO_SCORE`。[机器结果](evaluation/report.json)保留中心、尺寸、朝向、未来覆盖与膨胀全部null，**null不是零误差或零覆盖**。这是一项身体粒度缺口，不能据此断言原公交包络实际准确或错误。

匹配仍是历史数值/GT已暴露后的非盲助手审阅，未按误差选身份；投影只作身份检查，不以展示性z=0作为真值。若后续要评分，需先明确整车或分段身体对应关系，再独立建立评价域与合法轨迹；GT不反馈运行时。

[来源与检查](source_and_checks.json)记录两个辅助脚本及输入SHA。原两窗报告和原helper字节保持在`../helper_used_for_original_two.py`；复用helper仅将窗口计数改为实际配置长度，并把规划文字改为指向独立结果，不改先前机器报告。此次未触碰施工新在线输出。

```bash
cd /data2/hzy/MAP/agentic-driving
PYTHONPATH="$PWD/runs/tools/nuscenes-visualization/lib/python3.11/site-packages" \
  .venv/bin/python implementation/experiments/bus_actor_comparison.py \
  --review implementation/results/construction_recovery_20261029_v1/critical_actor_evaluation/bus/identity_review.json \
  --out /tmp/new_bus_actor_comparison
```

输出目录必须不存在。该命令仅独立离线评价，不发送模型请求。
