# 道路与未来占用：聚焦证据表

**新增真实调用0；只读7份指定证据，不改协议、工具、安全参数或旧实验。** 来源：[O1初答](../exp2_wire_enum_day_pilot_20261028_v1/runtime/transport/attempt_01/native_output.json)/[O2修订](../exp2_wire_enum_day_pilot_20261028_v1/runtime/transport/attempt_02/native_output.json)、[M1实际输入](../exp2_wire_enum_day_pilot_20261028_v1/runtime/transport/attempt_01/model_input.txt)/[M2实际输入](../exp2_wire_enum_day_pilot_20261028_v1/runtime/transport/attempt_02/model_input.txt)、[I1清单](../exp2_wire_enum_day_pilot_20261028_v1/runtime/transport/attempt_01/input_manifest.json)/[I2清单](../exp2_wire_enum_day_pilot_20261028_v1/runtime/transport/attempt_02/input_manifest.json)、[P提示](../../prompts/driving_v3_exp2.txt)，均与`cbebe35`字节一致。M1/M2均精确以P开头；两清单的10图SHA一致。

**输入事实：** 前向5帧覆盖2秒，其余五相机各1帧；标定在`capture/acquisition_metadata/calibrations`，六相机均有。四目标均仅引参考前向图F=`defde01fa5594f18b24be8fe68e6f2c4`，`time_id=nusc_us_1535489306412404`。未看图像像素，不能判断历史目标可见性或运动界是否可估。

**共同理由Q（原文明确绑定四个ID，路径均为`/hypotheses/unknowns/0/detail`）：** O1：“Occupancy remains null; observed crossing does not establish a future motion bound.” O2：“Historical crossing does not establish a future speed or turning bound.” O2同项还写“scenario inventories remain incomplete.”

| 原始JSON路径和值 | 模型明确理由及定位 | 输入/提示要求 | 分类；判断边界 |
|---|---|---|---|
| O1 `/hypotheses/road=null` | `/hypotheses/unknowns/3/detail`：“A supported metric road polygon, curb boundaries and exact stop-line position have not been established.” | 已给10图及标定；P23禁止扩大道路求解。该未知未绑定具体图像。 | 具体缺输入、尺度或前提，**无法判断**；`kind=not_provided`不证明输入文件缺失。 |
| O2 `/hypotheses/road=null` | 同指针：“Visible pavement and calibration alone do not justify filling or enlarging a road polygon to repair the rejection.” | P8：“Intrinsics/extrinsics alone do not establish metric scale.” | 有尺度支持限制的自述；是否唯一根因、为何不给局部条件patch，**无法判断**。 |
| O1/O2 `/hypotheses/objects/0/occupancy=null`；`ped_red` | 共同理由Q | F；P17保留不利模式、P20要求5秒整区间覆盖。 | **无法形成完整预测/运动界前提未给（自述）**；更细原因无法判断。 |
| O1/O2 `/hypotheses/objects/1/occupancy=null`；`ped_white` | 共同理由Q，确实绑定该ID | 同F、P17/P20。 | 同上；不是已确认的表达限制。 |
| O1/O2 `/hypotheses/objects/2/occupancy=null`；`ped_cart` | 共同理由Q，确实绑定该ID | 同F、P17/P20。 | 同上。 |
| O1/O2 `/hypotheses/objects/3/occupancy=null`；`ped_dark` | 共同理由Q，确实绑定该ID | 同F、P17/P20。 | 同上。 |

**提示是否冲突：未能确认。** P20写“Every retained scenario must cover [0,H] without any positive gap.”，也允许有依据的粗区间包络；P8写“Finite hypotheses/ranges are conditional and falsifiable”，P25仍明确条件式结论。可确认完整条件覆盖与仅给定性模式的差距，不能改称无条件真实保证。未见全图道路或精确停车线必须已知的条款；后者只是模型自述。不能由null速度反推占用缺失（P8允许共存），也不能挪用其他侧后目标的异步理由。表达限制、契约过严均无充分证据。

**唯一最小实验（`launched=false`）：** 检验模型是否混淆局部条件road patch与完整任务域覆盖。输入为同10图/采集/标定、原提示及两份原生输出，仅要求在现有`confidence_note`细分“不支持局部估计”与“不能完整覆盖”的具体缺项；允许“无法判断”，不补填road/occupancy或清除未知。拟独立预算最多**1次**生成请求，失败计入、无重试/修订，未经确认不启动；不接工具，仅评自报理由是否细化，不计完整链路成功。
