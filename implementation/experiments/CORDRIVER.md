# 单帧双系统复现

从同一 nuScenes 起算时刻重建因果 CorDriver 前端，按冻结规则取得三秒轨迹，再由上游评价器计算 ACR、CCR 和前缀 L2。视觉栈、优化器与独立连续几何检查器的环境及权重固定见[环境说明](../environment/README.md)。以下在仓库根目录运行。数据、GPU、项目凭据和产生的观测文件由运行者在本地管理。

## 用原始图像重现 scene-0221 第 23 帧

设置已安装的工具根目录、两个 Python 与获许可的 nuScenes 数据目录。下面的 SparseDrive 目录应提供环境说明登记的同帧边线、检测结果和验证索引。首次导入该模型的完整预测归档需要数 GB 内存。

```bash
export CORDRIVER_TOOLS=/path/to/tools
export CORDRIVER_CHECK_PYTHON=/path/to/control-env/bin/python
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
P="$CORDRIVER_TOOLS/cordriver_vision_env/bin/python"
D=/path/to/nuscenes
SPARSE=/path/to/SparseDrive
X=implementation/experiments
TOKEN=c4bdc2fcf6ae4ba599d53663ed2ab5c5
RUN="$(mktemp -d)"
"$P" -B "$X/cordriver_pipeline.py" prepare --data "$D" --study "$RUN/study" \
  --sparse "$SPARSE" --token "$TOKEN"
"$P" -B "$X/cordriver_pipeline.py" plan --data "$D" --study "$RUN/study" \
  --token "$TOKEN" --out "$RUN/plan" \
  --frozen-response "$X/cordriver_config/scene_0221_frame_23_response.json"
"$P" -B "$X/cordriver_pipeline.py" score --data "$D" --study "$RUN/study" \
  --token "$TOKEN" --plan "$RUN/plan/return.json" --out "$RUN/score.json"
```

1. `prepare` 从发布版验证索引按 token 查找当前帧和最多两个之前的同场景时刻。相机若比参考时刻晚，沿同一相机链定位最近的较早曝光并重新计算变换。使用实际六路原图、已采集 CAN 和无时间标签的推荐导航线，运行发布版权重的选定分支、原生 QP 或其回退。独立记录预测边界、参与者及各运动模式。缺少所需的历史、图像或导航时报告失败。可省略 `--sparse` 单独运行缺第二路预测的配置，此时输出显式记录该路不可用。
2. `plan` 先绑定同一 token 的前端结果、原生 QP 状态、图像包、模型身份及权重。原生 QP 回退或失去核对依据、预测参与者相交、采样法向加速度过大均可触发 Astra。原冻结的请求含六张原图、一张前视投影、内外参、因果导航、自车测量和优化前初轨。上述例子将重建请求的整包 SHA-256 与已收录的脱敏收据逐字节核对，再使用原来实际得到的两个有向目标窗与速度。使用不同硬件或图像编码导致摘要变化时，入口显式拒绝回放。
3. `KEEP` 保留原生。对 `ADJUST`，先用相同输入重建固定走廊参考的预测净距要求，再全时域平滑并生成、重积分和核验候选。未触发慢系统时，对原生的车身扫掠检查预测路缘。主路缘触发使用确定性净距优化，只有次路缘触发时使用双来源边界重拟合和 0.925 m 进展保护。候选未获核验时保留实际原生及原因。规划产物在 `plan/return.json`，包括实际采用轨迹的六个 LiDAR 平面点、来源、触发记录以及选中候选和回执的链接。
4. `score` 在独立进程从原评价地图、参与者和录制未来计算 1、2、3 秒分数。0221/23 的本地发布验证中，3 秒 CCR 从 1 到 0，ACR 均为 0，L2 从 0.610961 m 到 0.396696 m。这是已暴露案例的开环复现。

## 其他帧

将 `TOKEN` 换为验证集中相应的 sample token，使用全新的 study 与 plan 目录。在相同 study 上另用新输出目录和 `--fast-only` 可运行同输入纯快基线，不读模型回执。对未触发慢系统的 token，`plan` 可直接运行。对需要慢系统的 token，可以用 `--responses /path/to/private/astra_live` 提供原请求、服务原生返回及逐帧收据，入口会核对实际图像字节、整包和模型身份。需要重新生成时，显式给 `--live` 并通过环境变量 `OPENAI_API_KEY` 或独立的 `DRIVING_API_KEY_FILE` 提供有权访问 `gpt-6.2-astra` 的凭据。一次 plan 对同一 token 最多一次 POST，在发送前持久化预扣记录，错误后退出。新服务采样可能给出不同指令。

不使用预置收据的跨帧研究应在发请求前登记 token、研究分母与预算，分别报告原生、纯快系统以及双系统的实际返回。本入口保存方法的逐帧结果。原 99 帧的发布汇总另以冻结评价表复算，重做一帧不改变该表。
