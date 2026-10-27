# nuScenes 四类真实样例：接入与可行性测试记录

2026-10-27，项目 `/data2/hzy/MAP/agentic-driving`。

**结论：四例真实数据接入、解码、因果时间选择和几何检查通过；真实多模态驾驶方案可行性尚未验证。** 当前没有 DRIVING_API_BASE、DRIVING_MODEL、DRIVING_API_KEY，批量入口在认证配置检查处返回退出码 2，真实 HTTP 请求/模型响应、四例 T1–T4 运行及模拟执行均为 0。没有伪造模型输出，没有 G2 分数或“已通过”结论。

## 数据与样例

参考 `/data2/hzy/MAP/SparseDrive/data` 后发现其 nuscenes 链接指向已不存在的 `/data4/nuscenes_dataset`。实际完整数据位于 `/data/nuscenes_dataset`，本次读取 `v1.0-mini`、真实 samples/sweeps 图像和地图扩展；没有修改原始数据、SparseDrive 链接或 DAWL。

| 样例 | scene | 自车因果速度 m/s | 离线焦点位置 (x,y) m | 离线焦点速度 (vx,vy) m/s |
|---|---|---:|---|---|
| 白天行人横穿 | scene-0553 | 0.00 | (7.418, 0.368) | (-0.184, -1.931) |
| 施工障碍/弯道 | scene-0061 | 4.98 | (16.624, 0.794) | (0.000, 0.000) |
| 路口横向来车 | scene-0757 | 0.00 | (17.221, 5.056) | (-1.427, -7.544) |
| 夜间行人横穿 | scene-1100 | 0.11 | (18.693, -0.324) | (-0.068, 0.793) |

目标位置/速度来自隔离的离线标注，仅描述测试样例，并非模型预测值。每例选取 10 张真实图像（前视历史＋六相机最近非未来帧），合计 40 张 1600×900 RGB 图像。前三例白天，末例夜间复杂光照。已人工查看参考帧，拼图见 [sample_contact_sheet.jpg](sample_contact_sheet.jpg)；这项查看只用于典型性审阅，不产生或冒充模型接口输出。

## 实际执行的检查

- 使用项目独立 `.venv` 和 Pillow 11.3.0 解码全部 40 张图像，verify 与像素读取均通过，见 [image_checks.json](image_checks.json)。未调用检测、分割、深度、跟踪或预测模型。
- 每例采集窗口至多 2s，所有输入帧时间不晚于 CAM_FRONT 参考图像，保留实际 UTC、微秒 time_id 和图像 SHA256。施工例实际历史 1.9s，其余 2s，没有伪造历史采集记录。
- nuScenes 采集/标注参考时刻存在约 34–37ms 差异：运行时采用真实图像采集时间，离线评价按该时间对齐标注，不把 sample 时间当作相机时间。
- nuScenes native ego 原点为后轴；运行时标定和离线目标均按显式中心偏移转换。采用独立齐次矩阵逆变换交叉核对，四例最大数值残差约 2.53×10^-13m。该数值证明变换实现自洽，不是物理测量精度。
- 导出隔离离线真值：49 个 ROI 内且未来标注完整的目标、1225 个 0.2s 占用区间，以及带洞/多连通区域的地图 drivable_area。图像哈希与运行输入一致。排除断裂或未来不完整的标注并记录原因。
- 完整自动测试 **68/68 通过，4.775s，0 skipped**：包括原 54 项、nuScenes 坐标/四元数/标定扩展/原格式兼容、地图洞保留，以及六项启用真实数据的集成检查。见 [tests.txt](tests.txt)。依赖检查、Python 编译与 git diff --check 通过。
- 真实模型批量入口实际运行并逐例记录 `ACQUISITION_CHECKS_PASS` / `BLOCKED_MISSING_MODEL_CONFIGURATION`。见 [model_attempt.json](model_attempt.json)。返回失败状态，没有用人工场景或标注替代模型输出。

## 已新增的可运行工程

`agentic/datasets/nuscenes_capture.py` 实现采集导出，表白名单只有 sample/sample_data/ego_pose/sensor/calibrated_sensor；不读取目标标注、类别或地图。capture 的可选 acquisition_metadata 保存确定性内外参、后轴到中心的显式偏移、自车历史和速度来源；旧 v2 输入仍被接受。

`experiments/nuscenes/prepare.py` 是单独的离线准备入口，目标/地图只写入 offline_evaluation。在线批量 `run_cases.py` 只接收 runtime_index 和 runtime_inputs，没有 GT 或地图参数。每例配置就绪后执行三次重复模型调用，并另起一次每轮必调模型的四工具规划/有限失败修订/模拟短前缀流程。

G2 新增地图 GeoJSON 与相同 ROI 下的道路边界/面积 IoU 计算，保留多连通区域和孔洞；旧道路多边形字段继续兼容。未设置或应用验收阈值。

准备结果位于 `/data2/hzy/MAP/agentic-driving/runs/nuscenes_cases_20261027/`，运行输入副本随本报告保存于 `runtime_inputs/`；完整离线真值在原准备目录的 `offline_evaluation/truth.json`。模型前置检查运行目录见 validation_summary.json。所有数据读取均未修改原数据集。

## 科学与物理边界

这四例是人工挑选的 mini 典型窗口，不能推广为全数据集结论。车辆尺寸、轴距、中心偏移、安全间距为显式研究假设，不是 nuScenes 车辆实测值。自车速度为过去约 0.5s 定位记录的因果后向差分及前向投影，非直接 CAN 瞬时速度，停车噪声会截至零。

目标未来区间来自约 2Hz 离散标注的线性中心插值和外接圆包络，只覆盖插值代理，不能声称知道真实采样间连续轨迹。未来完整目标筛选有存活偏差，原始可见比例不是当前相机的已校准遮挡概率。地图仅作离线几何评价，不能补入模型场景。

真实模型与 GT 的对象关联待模型输出后审阅，目前 model_object_ids 为空。视觉尺度、目标运动/未来占用质量、四例工具成功率、短前缀有效性以及 G2 均无实测结论。现阶段只确认这些样例已具备可复现的真实数据入口与隔离评价条件。

## 继续运行

[样例运行说明](../../experiments/nuscenes/README.md) 给出准备、解码、测试、真实模型批量和 G2 命令。当前准备数据可直接执行：

```bash
cd /data2/hzy/MAP/agentic-driving
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
NUSCENES_CASES_DIR="$PWD/runs/nuscenes_cases_20261027" \
  .venv/bin/python -m unittest discover -s implementation/tests -v

# 安全配置真实服务后；不要将密钥写入聊天或提交仓库
.venv/bin/python implementation/experiments/nuscenes/run_cases.py \
  --index runs/nuscenes_cases_20261027/runtime_index.json --runs runs --repeats 3
```
