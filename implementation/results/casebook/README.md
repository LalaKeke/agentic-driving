# 案例、工具 I/O 与轨迹叠加

优先打开 [交互案例集](index.html) 或 [十三页 PDF](casebook.pdf)。包含四个真实 nuScenes 窗口、两个人工链路视图，共 11 个环节、66 张工具 I/O 卡片。每例可选择调用轮次，再点击 Agent、T1、T2、T3、T4、Execute，查看摘要、完整 JSON 并下载。

**真实模型未生成轨迹；真实相机图上的未来曲线均为离线参考。** 原模型输出和工具拒绝记录不变，未新增模型调用。未调用的 34 个工具环节保留空输入/空输出，NOT_CALLED 是报告标记，不冒充工具返回。工具入参由原始采集、模型及上游产物组成，不声称当时另存了函数级跟踪。

| 案例 | 场景图与轨迹 | 调用摘要 | 实际结论 |
|---|---|---|---|
| 白天行人横穿 | [PNG](day_pedestrian_crossing.png) / [SVG](day_pedestrian_crossing.svg) | [图](day_pedestrian_crossing_calls.png) | 初次和两次修订均在 T1 因无界未知拒绝 |
| 施工障碍与左弯 | [PNG](construction_obstacle.png) / [SVG](construction_obstacle.svg) | [图](construction_obstacle_calls.png) | 初次规划及修订均被 T1 拒绝；保留原超时记录 |
| 路口横向车流 | [PNG](intersection_cross_traffic.png) / [SVG](intersection_cross_traffic.svg) | [图](intersection_cross_traffic_calls.png) | T1 缺少尺度依据；反馈请求超时，原日志保留 |
| 夜间行人 | [PNG](night_pedestrian_crossing.png) / [SVG](night_pedestrian_crossing.svg) | [图](night_pedestrian_crossing_calls.png) | 原协议超时；独立 2.1 格式复核的离线 T1 仍缺尺度，不合并协议 |
| 人工停车与修订 | [PNG](synthetic_stop.png) / [SVG](synthetic_stop.svg) | [图](synthetic_stop_calls.png) | T4 拒绝注入的匀速候选；修订制动规划通过，仅释放 0.2s |
| 人工恢复行驶 | [PNG](synthetic_resume.png) / [SVG](synthetic_resume.svg) | [图](synthetic_resume_calls.png) | 新观测后重新调用四工具，核验通过后模拟恢复 |

## 直接检查工具输入输出

以下是人工制动修订分支的完整 I/O 文件，而不只是接口定义：

- [Agent](io/synthetic_stop/01-Agent.json)：采集与失败反馈 → 语义决策、米制假设和来源。
- [T1](io/synthetic_stop/01-T1.json)：采集、智能体输出与来源 → 米制场景；坐标、时间与修订一致。
- [T2](io/synthetic_stop/01-T2.json)：场景、内含车辆外形及安全配置 → 25 个整区间后轴配置空间单元。
- [T3](io/synthetic_stop/01-T3.json)：米制场景与走廊 → 26 个带时间的自行车模型轨迹点和控制量。
- [T4](io/synthetic_stop/01-T4.json)：原米制场景与轨迹 → PASS、净空下界、仅 0.2s 放行；完整约束条件保留。
- [模拟执行](io/synthetic_stop/01-Execute.json)：通过的核验与同一观测版本 → 积分前缀后速度 5.4m/s、中心位移约 1.14m。

对照 [故障分支的 T3](io/synthetic_stop/00-T3.json) 和 [T4](io/synthetic_stop/00-T4.json)：T3 求解后被显式替换为匀速候选。其原 solver.status/objective 来自注入前求解，不能作为当前候选可行证据。T4 报告 BODY_COLLISION、STOP_BOUNDARY、STOP_DEADLINE，含时间区间、位置、计算值、阈值和修订请求，释放 0s。

对照真实 [白天例 T1](io/day_pedestrian_crossing/00-T1.json)：真实模型结构化输出仍有 unbounded_unknown，原运行在 T1 失败，没有调用 T2–T4。图上的 GT 轨迹不补入这些工具输入。

## 官方 nuScenes 可视化与轨迹含义

已在本项目独立环境实际运行 **nuscenes-devkit 1.2.0**，使用 [官方教程](https://www.nuscenes.org/tutorials/nuscenes_tutorial.html) 中的 `NuScenes.render_sample_data` 渲染相机背景，并用官方 `transform_matrix` / `view_points` 叠加轨迹；对应实现来自 [官方源码](https://github.com/nutonomy/nuscenes-devkit/blob/master/python-sdk/nuscenes/nuscenes.py)。叠加层由本项目实现，不能称官方轨迹优化器。库版本及三个官方源码文件哈希在 [manifest.json](manifest.json)。

- 橙色／青色：焦点／辅助目标 **GT 框底部中心三维位置的线性插值**，0–5s、0.2s 步长，不是模型预测。BEV 的星号是采集时刻位置。
- 紫色虚线：随后实际采集的自车定位参考，含显式 1.2m 研究中心偏移；既不是已执行的反事实规划，也不是实测车身中心。
- BEV 蓝／红叉：已有真实模型点位，仅两个探索性目标各两次；不外推总体精度。模型没有可信高度／地面平面，故不强行投影到相机图。
- 深灰矩形：研究车辆形状示意；不是 nuScenes 实测车形。
- 人工图绿色轨迹：已有 T3 规划；粗绿段是 T4 释放的 0.2s。红虚线为被拒候选；橙色区域为脚本整区间占用，浅青区域约束后轴参考点，避免重复车身膨胀。

投影采用真实参考相机时刻的自车姿态、外参、内参；独立矩阵方式交叉核对。目标的原约 2Hz 标注插值到相机参考时刻，不能当作测得的连续时间真值。图像视野与 BEV 轴窗之外不显示，完整轨迹点保留在审计文件；只按正深度与图像边界裁剪，不推断遮挡；画出的曲线可穿过图中前景物体。自车定位没有被误当作模拟执行反事实的结果。详见 [逐点投影审计](projection_audit.json)，包含原标注对、时间、三维位置、像素位置及裁剪标志。渲染图像 SHA256 与真实在线输入逐份核对。

## 人工完整闭环来源与复核

使用后续已验收运行 `runs/20261027T110115Z-3ac5ecb5`：26 个 0.2s 前缀，最低记录速度 **0**、最后约 **2m/s**，[累计速度图](synthetic_closed_loop.png)。此运行已修正相对停车截止时间；与先前打开的 `20261027T105505Z-8a812146` 近停运行分别保留，不覆盖历史。来源记录中的 code_dirty=true 原样披露，不称干净提交上的新实验。

三个选定候选已经用当前独立 T4 重新核验，完整输出与历史记录相等：[复核结果](t4_reverification.json)。复核没有新求解、优化器更新或执行。所有 PASS 以模型/脚本场景假设及整区间覆盖成立为条件；人工数据不构成真实视觉驾驶通过或 G2 通过证据。

## 复现与检查

```bash
cd /data2/hzy/MAP/agentic-driving
# 当前项目内独立可视化环境已安装，未修改其他项目环境。
runs/tools/nuscenes-visualization/bin/python \
  implementation/visualization/build_casebook.py

# 新机器仅在独立环境安装锁定报告依赖；不需修改驾驶项目 .venv。
python -m venv runs/tools/nuscenes-visualization
runs/tools/nuscenes-visualization/bin/python -m pip install \
  -r implementation/visualization/requirements-nuscenes.lock.txt
```

构建需 nuScenes 原数据、已归档真实输出和上述人工运行；浏览已生成 HTML/PDF 无需联网、认证或 Python。官方文档主要测试 Python 3.9/3.12，本服务器独立 Python 3.11.5 已实际完成渲染与依赖检查；锁文件记录实际安装组合。

[浏览器检查](browser_checks.json) 验证六个案例、11 个环节、66 张 I/O 卡片、空调用、JSON 下载与 390px 窄屏，JavaScript 错误/外部请求均为 0。这些是产物检查，不冒称新驾驶实验或新增 92 项软件测试。

来源和投影一致性检查见 [offline_checks.json](offline_checks.json)：66 份 I/O 与上游产物一致，247 项来源哈希核对，156 个目标 GT 投影点与离线评价坐标交叉核对；既有 121 份实验/可视化证据保持原字节。
