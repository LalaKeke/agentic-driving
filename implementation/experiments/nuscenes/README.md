# nuScenes 典型样例接入与测试

实际原始数据根目录为 `/data/nuscenes_dataset`。`/data2/hzy/MAP/SparseDrive/data/nuscenes` 是指向旧 `/data4/nuscenes_dataset` 的失效链接；本工程没有修改链接或 SparseDrive/DAWL。使用官方 v1.0-mini 的四个真实窗口，各取历史前视图像及六相机的最近非未来帧，共 40 张图像。原数据集只读，生成文件写入本项目 runs。

| 样例 | 场景 | 离线筛选目标 | 自车速度（因果估计） |
|---|---|---|---|
| day_pedestrian_crossing | scene-0553 | 白天约 7.4m 行人横穿，参考图像可见多人和红灯 | 约 0m/s |
| construction_obstacle | scene-0061 | 约 16.6m 施工车辆，参考图像可见围挡、工程机械、弯道 | 约 4.98m/s |
| intersection_cross_traffic | scene-0757 | 约 17.2m 前方横向车流，参考图像可见交叉路口 | 约 0m/s |
| night_pedestrian_crossing | scene-1100 | 约 18.7m 夜间行人、信号强光 | 约 0.11m/s |

表中距离/速度来自离线标注筛选和自车采集信息，不是模型测量结果。样例描述、焦点对象、未来标注和地图不会送入模型；runtime 的 scene_id 只含原始 sample token。人工查看拼图只用于确认典型性，不生成任何假冒模型输出。

## 运行

```bash
cd /data2/hzy/MAP/agentic-driving
# 当前 .venv 已安装；其他环境按锁定依赖安装
.venv/bin/python -m pip install -r implementation/requirements.lock.txt
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1

# 1. 从只读原始数据生成一组新的运行输入和隔离真值；输出目录必须不存在
.venv/bin/python implementation/experiments/nuscenes/prepare.py \
  --config implementation/experiments/nuscenes/config.json \
  --out runs/nuscenes_new

# 2. 实际解码图像并生成仅供离线审阅的拼图
.venv/bin/python implementation/experiments/nuscenes/verify_images.py \
  --cases runs/nuscenes_new

# 3. 跑全部测试，并启用真实数据集成检查
NUSCENES_CASES_DIR="$PWD/runs/nuscenes_new" \
  .venv/bin/python -m unittest discover -s implementation/tests -v

# 4. 真实模型重复调用 + 每例四工具/失败修订/模拟短前缀
# 先安全导出 .env.example 中的 DRIVING_API_BASE、DRIVING_MODEL、DRIVING_API_KEY
.venv/bin/python implementation/experiments/nuscenes/run_cases.py \
  --index runs/nuscenes_new/runtime_index.json --runs runs --repeats 3
```

没有认证/模型配置时，第 4 步仍检查四例采集输入，逐例写入 BLOCKED_MISSING_MODEL_CONFIGURATION，以退出码 2 结束，不生成模型输出、轨迹或 G2 指标。配置就绪后会对每例先调用三次真实模型并保存 G2 用 model-record，再新调用智能体开始一次规划；工具失败最多进行两次新模型修订。每例对应一个观测窗口，这不是对原记录的闭环控制仿真。通过后只有模拟前缀，不能把记录中的后续真实自车状态当成执行该反事实前缀的结果。

模型批处理只读取 runtime_index、runtime_inputs 和真实图像，没有真值/地图参数。所有模型调用日志由 RunLog 编号保存。重复调用输出和规划调用分开记录；传输重试、未通过响应校验的请求不计为有效结构化响应。

## 数据隔离与物理约定

- `agentic/datasets/nuscenes_capture.py` 仅使用 sample/sample_data/ego_pose/sensor/calibrated_sensor。输出 acquisition_metadata 是可选向后兼容字段，不改变既有 v2 字段含义。所有图像及 ego_history 的采集时间不晚于参考帧，历史窗口最多 2s；施工例真实历史跨度 1.9s，不伪造缺失帧。
- 运行时保留实际图像 ID、采集微秒 ID、UTC、相机内外参和相对自车位姿。原始图像相机标定是 sensor→native ego；native ego 为后轴，按显式 d 假设转换为本项目车身中心。相机历史位姿通过采集自车位姿表达，不用于自动预测或对齐目标状态。
- 自车速度使用过去约 0.5 秒定位记录的后向差分和当前前向投影，停车时的微小负噪声截至零；这不是直接 CAN 瞬时速度。仅访问参考时刻及此前的自车状态，未使用未来自车路径决定导航目标。
- 车辆尺寸、轴距、中心偏移 d=1.2m、运动界限和 1m 间距是显式研究配置，未声称是 nuScenes 车辆实测值。中心偏移在标定和离线真值中一致使用，正式物理评价需核对实际车身几何。
- `offline_evaluation/truth.json` 单独存目标标注、可见比例标签、地图 drivable_area（保留洞及多连通区域）、未来区间包络和图像哈希。原始标注在 sample 时刻，参考相机比其早约 34–37ms；评价端按实际相机时刻做标注轨迹插值，不能直接把 sample 时刻当作图像时刻。
- 未来 25 个 0.2s 区间的包络来自离散标注中心的分段线性插值，并用车身外接圆保守覆盖旋转；最大标注间隙 0.75s。它只覆盖这套插值代理，不是测得的真实连续时间运动界。缺失未来或断裂轨迹显式排除并记录，存在存活轨迹筛选偏差，不能据此报告全数据集检出率。
- 场景筛选是 offline 的特权操作；不会把 case_id、原场景描述、焦点 annotation token、目标状态、GT 路线或地图送入模型。真实模型输出与 GT 的目标关联尚待审阅，model_object_ids 为空。

## G2

G2 新增对离线地图 GeoJSON 的支持，保持多边形洞、多连通区域；预测与地图使用相同 ROI 裁剪，记录道路边界 Hausdorff 和面积 IoU。旧 road_polygon_m 输入继续兼容。ROI、距离分层、完整轨迹可用性要求是实验配置，不是通过阈值；任何指标都不自动给出 G2 通过结论。

真实模型调用后，先人工核对同一 sample_id 的输出和目标，将对应 GT instance token → 模型对象 ID 写入独立真值副本的 model_object_ids。不得把关联结果送回运行输入。之后：

```bash
cd implementation
../.venv/bin/python -m agentic.cli g2 \
  --records ../runs/实际模型运行目录/*-model-record.json \
  --truth ../runs/nuscenes_new/offline_evaluation/reviewed_truth.json \
  --config examples/v2/g2_config.json --out /tmp/nuscenes-g2.json
```

当前真实模型配置/认证缺失，因此尚无模型米制误差、未来占用覆盖得分、四例规划成功率或 G2 可行性结论。已完成的是实际数据准备、解码、时间与坐标检查及软件回归。不能将这些通过项解读为视觉驾驶方案通过。

官方依据：[nuScenes 数据格式](https://www.nuscenes.org/data-format)、[官方 schema](https://github.com/nutonomy/nuscenes-devkit/blob/master/docs/schema_nuscenes.md)、[官方插值实现](https://github.com/nutonomy/nuscenes-devkit/blob/master/python-sdk/nuscenes/nuscenes.py)。本次也读取了服务器上的官方 devkit schema。
