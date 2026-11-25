# nuScenes 开环复算

## 正式评测口径（2026-11-24 冻结）

主表对应 CorDriver 论文 Table I 的 **CorDriver** 行。使用[发布提交](https://github.com/Fudan-MAGIC-Lab/Drive-in-Corridors/tree/5a0ef06cb84156f6fa8510f5bf584403bf91c3dc)、stage-2 learned-weight 权重和配套 validation infos，按场景保留时序 BEV。上游 stage-2 测试配置虽有 `clip=True`，该提交实际送入 QP 的仍是预测走廊，使用细化走廊的那一行被注释。[权重、索引及评分代码摘要](../environment/assets.json) · [QP 实际调用](https://github.com/Fudan-MAGIC-Lab/Drive-in-Corridors/blob/5a0ef06cb84156f6fa8510f5bf584403bf91c3dc/projects/mmdet3d_plugin/VAD/VAD_head.py#L930)

| 评价项 | 固定规则 |
|---|---|
| 分母 | 官方验证集 150 个 scene、6,019 个起算帧。推理按原时序遍历。主表使用 `fut_valid_flag=True` 的全部 5,119 帧，900 帧未来不完整。所有方法按同一 sample token 配对。 |
| 计分轨迹 | 使用各方法实际返回的最终轨迹。原生取导航分支的 `ego_fut_mpc`，将相对位移累加为当前 LiDAR 坐标系下的 6 个米制位置，时刻为 0.5–3.0 秒。双系统按相同坐标和时刻提交结果。优化失败而实际回退时照常计分，同时记录回退来源。确无输出时报告覆盖缺口，主表保持未完成。 |
| L2（m） | 对 1、2、3 秒前缀内的 2、4、6 个位置分别求与专家同刻位置的欧氏距离均值，再跨有效帧平均。 |
| ACR／CCR（%） | 使用上游车身栅格器。ACR 对车辆与行人占用，CCR 对地图 `boundary`。逐采样时刻、逐指标屏蔽录制自车自身发生同类碰撞的位置。每个前缀其余采样点有一次命中，该帧即计 1；命中帧数除以 5,119。 |
| Avg. | 分别对各指标未取整的 1、2、3 秒结果等权平均，最后才四舍五入。 |

[上游选取返回轨迹和指标](https://github.com/Fudan-MAGIC-Lab/Drive-in-Corridors/blob/5a0ef06cb84156f6fa8510f5bf584403bf91c3dc/projects/mmdet3d_plugin/VAD/VAD.py#L468) · [逐时刻真值屏蔽](https://github.com/Fudan-MAGIC-Lab/Drive-in-Corridors/blob/5a0ef06cb84156f6fa8510f5bf584403bf91c3dc/projects/mmdet3d_plugin/VAD/planner/metric_bp.py#L399) · [有效帧汇总](https://github.com/Fudan-MAGIC-Lab/Drive-in-Corridors/blob/5a0ef06cb84156f6fa8510f5bf584403bf91c3dc/projects/mmdet3d_plugin/datasets/nuscenes_vad_dataset.py#L1800)

**公平比较还要固定给规划器的信息。** 发布版完整时序推理单列为论文复现基准。作者给定的导航指令由未来自车位移派生。比较论文基准设置下的规划改动时，应共用同一发布版前端、导航分支和初轨，并明示该 benchmark 条件。检验系统收益时，同输入原生、仅快系统、快系统＋Astra 共用同一帧、标定、因果导航、BEV 历史与原生前端输出。检验 Astra 增量时再固定两臂的地图／障碍预测来源、走廊平滑、求解预算和回退，唯一实验差异为是否启用 Astra。评分真值在提交最终轨迹后读取。所有有效帧均计入，包括未触发以及原生无碰撞的帧，才能同时测到救回与新增的碰撞。若某臂缺历史或导航，记录预先规定且能够实际返回的同输入回退或记为缺输出。99 帧碰撞富集集与按单项指标剔除整帧真值碰撞的结果保留作诊断。

**原生校准。** 对此前全量推理的四份原始逐帧记录重新汇总，1／2／3 秒 ACR 命中 1／3／16 帧，CCR 命中 8／31／103 帧，按论文精度六个碰撞率全部吻合。L2 为 0.179073／0.342071／0.597090 m，3 秒显示为 0.60 m，论文为 0.59 m，保留差异。另按 token 排序，从 ACR 命中、CCR 命中、均未命中各取 QP 正常和回退各两帧，用实际返回轨迹及原始标签独立重评。12 帧各时域碰撞标志全部吻合，L2 最大绝对差为 0.00000066 m。[作者发布结果](https://github.com/Fudan-MAGIC-Lab/Drive-in-Corridors#results)

从仓库根目录复算原生全量表。`full-val` 校验有效 token 集合摘要，支持只提交全体有效帧或提交含全部 900 个无效帧的完整索引，重复、缺项或用等量其他帧替换均会拒绝。逐帧先用[规划流程的 score](../experiments/CORDRIVER.md)核对返回轨迹并打分；其 JSON 或合并后的 JSONL 用 `--source paired` 汇总。同一批文件须来自同一冻结实验臂；该命令检查计分覆盖，前端与方法身份另据运行清单和逐帧收据核对。

```bash
OFFICIAL=../.agentic-driving-archive/pre-cordriver-release-20261121/implementation/results/cordriver_collision_cohort_20261119_v1/official
python3 -B implementation/evaluation/review.py full-val --source official "$OFFICIAL"/shard_*/rows.jsonl
```

## 历史碰撞子集

从仓库根目录运行。指标汇总只需要 Python 3 标准库，可在干净检出中直接运行。

```bash
python3 -B implementation/evaluation/review.py metrics
```

[逐帧评分](per_frame.csv)保留 CorDriver 发布评测中 5,119 个有效起算帧里、3 秒内有参与者或路缘碰撞的全部 119 帧。其中 99 帧具备完整的因果输入。6 帧缺历史，14 帧缺合规导航，均保留原生发布版评分和缺项原因。以下是对这些保存的逐帧评分重新汇总的结果。重跑感知、规划及标签评分需要主流程的模型、数据和环境。

| 方法 | 可比帧数 | 3 秒 ACR 帧数 | 3 秒 CCR 帧数 | 3 秒任一碰撞帧数 | 1／2／3 秒 L2 均值，m |
|---|---:|---:|---:|---:|---:|
| 同输入原生 CorDriver | 99 | 6 | 57 | 63 | 0.67144 |
| 纯快系统 | 99 | 6 | 45 | 51 | 0.67044 |
| Astra 双系统 | 99 | 5 | 46 | 51 | 0.66963 |

ACR 按参与者占用、CCR 按路缘计分，使用发布评分器的完整车身和录制自车屏蔽口径。每个时域的 L2 取该前缀内半秒采样的平均距离，最右列对三个时域等权。按发布版返回轨迹选帧时的 BEV 历史和导航分支可能与部署前端不同。双系统相对同输入原生救回 12 帧、新增 0 帧。相对纯快系统救回 2 帧、新增 2 帧。99 帧中 40 帧触发 Astra，其余 59 帧依冻结策略采用快系统。样本经过碰撞富集，相邻起算帧可覆盖同一交互，结果只适用于这批帧。

[另一个自车历史消融的 39 次真实判断](judgments.csv)与主表部分重叠。Astra 审阅的初轨有 23 条命中开环碰撞，其中 19 次要求调整。初轨无碰撞的 16 帧中有 9 次要求调整。两节点位置和速度均覆盖录制状态的指令为 5/28；其他合理避让动作仍可能实现任务。

### 按指标和时域过滤录制自车碰撞

项目的条件评价逐指标计算分母。对于 1、2、3 秒前缀，分别检查从 0.5 秒至该时域终点的完整录制车身。CCR 只剔除该前缀真值碰到地图路缘的帧，ACR 只剔除该前缀真值碰到参与者占用的帧。L2 使用全部有有效真值和方法输出的帧。如果报告 ANY（任一碰撞），才按两类真值命中的并集排除。配对方法在同一指标、时域下使用相同可评分帧。上面的冻结表沿用 CorDriver 发布版逐采样时刻屏蔽规则。

评价入口接受含唯一 `token` 列的冻结 CSV，输出 `frames.csv`，逐帧列出两类真值的命中时刻和各指标资格。若输入另含形如 `fast_CCR_3s` 的指标列，`metrics.csv` 给出每个方法、时域与指标的命中数、有效分母及均值。缺少方法结果单列。可加 `--retain CCR --horizon 3` 导出专用于该指标的帧清单。路缘图册默认只使用三秒 CCR 资格；`--policy published` 重现发布版图册，`--policy gt-clean` 重现此前按真值两类并集筛帧的图册。

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  runs/tools/cordriver_vision_env/bin/python -B implementation/evaluation/recorded_ego_filter.py \
  --cohort implementation/evaluation/per_frame.csv --out /tmp/cordriver-gt-per-metric
```

## 从候选和真实决定重跑轨迹选择

```bash
python3 -B implementation/evaluation/review.py dispatch --out /tmp/cordriver-returned-tracks.json
```

输出文件必须尚不存在，可省略 `--out` 只验证。只需 Python 标准库和仓库内的[冻结输入](dispatch_inputs.json)。99 帧的输入保存了同输入原生车身位置和航向、QP 回退、法向加速度与预测车身相交时刻，以及 40 次真实 Astra 的结构化决定和目标窗。198 个快系统候选与 101 个慢系统候选逐项保存实际轨迹、当次求解的数值或布尔验收结果及原始收据摘要。评测标签、录制未来与输出期望值不参与选择。

入口重算触发、快系统边界改善门槛、候选排序和第二边界分支的进展否决。慢系统在触发时按真实 KEEP 保留原生，ADJUST 按原顺序重算运动裕量、目标窗和间距底线，取首个合格候选；未触发时沿用快系统。所有回合完成后，才读取[原归档的期望摘要](dispatch_expected.json)，逐帧比对候选 SHA、收据 SHA、完整时序与车身坐标。99 × 2 个输出均匹配，快系统采用替代轨迹 19 次，双系统 26 次。该入口从当次已经求出的候选及物理验收收据开始；重新生成候选与重做几何检查属于规划流程。

## 前视图

[四个冻结样例](front_examples.json)包含 3 秒车身中心轨迹、两个有向目标窗、按实测相机及曝光变换计算的可见线段像素、前视原图文件名与 SHA-256。在本地 nuScenes v1.0-trainval 的原图上重画；绘图依赖 Pillow ≥ 9.1。蓝白线为 Astra 所见的初轨，橙线为同输入原生实际返回，青线为该案例采用的调整轨迹。金色为 1.5 s 和 3.0 s 目标窗；出画的区域可在右侧俯视图看到。显示用 z=0 平面投影缺少地形和遮挡修正。

```bash
python3 -B implementation/evaluation/review.py front \
  --dataroot /path/to/nuscenes --case 0018/31 --out /tmp/0018-31.png
```

可选 0018/31、0221/23、0221/25、1062/20。脚本在读取本地原图后核对 SHA、图像宽高；参数中的数据根目录下应存在 `samples/CAM_FRONT/`。

| 场景／帧 | 具体证据 | 同输入原生 → 该案例轨迹的 3 秒 L2，m |
|---|---|---:|
| 0018/31 | 在已暴露案例中，将第二套预测边界带入求解后得到近似解。原生 CCR=1，修复 CCR=0；次道路检查仍为 UNKNOWN。该修复单列于冻结 99 帧主表之外。 | 2.028 → 0.353 |
| 0221/23 | 无显式自车历史的 Astra 要求调整。纯快系统和固定 KEEP＋平滑均仍命中 CCR。 | 0.611 → 0.397 |
| 0221/25 | 带自车历史的 Astra 要求调整。纯快系统也避开路缘。 | 0.631 → 0.499 |
| 1062/20 | 带自车历史的 Astra 要求调整。纯快系统也避开路缘，3 秒 L2 为 1.026 m。 | 0.735 → 0.272 |

[150 个场景的筛查表](screen.csv)提供有效帧分母与入选帧序号，复算入口逐项匹配场景和帧。[来源摘要](source.json)保存归档评分、筛查、判断表及样例取证脚本的 SHA-256。图中独立评分仅用于评价显示。

## 全部 119 帧碰撞图册

准备上游 CorDriver 的 `corridor_nuscenes_infos_temporal_val.pkl`、nuScenes 的 `samples/CAM_FRONT` 原图，以及 NumPy、SciPy、Pillow 和 Noto Sans CJK 字体。Ubuntu 的字体文件位于 `/usr/share/fonts/opentype/noto/`。从仓库根目录执行下面的命令，输出指定一个新目录。

```bash
python3 -B implementation/evaluation/atlas.py \
  --infos /path/to/corridor_nuscenes_infos_temporal_val.pkl \
  --dataroot /path/to/nuscenes --out /tmp/cordriver-collision-atlas
```

打开输出目录的 `index.html` 可筛选全部 119 张高清图。每张对照同一曝光的前视投影、等比例三秒俯视及 ACR／CCR／L2。20 帧缺因果输入，保留发布版轨迹和独立录制参照。[图册数值包](atlas_tracks.json)只保存各方法实际返回的计分轨迹、前视标定、原图文件名与 SHA。图册入口先核验验证索引 SHA-256，再从本地文件按样本 token、场景、帧号和时间读取六个有效的 `gt_ego_fut_trajs` 位移并累加。六个已计分点和图上显示原点分别处理。程序检查每条轨迹 1、2、3 秒 L2 是否与公布分数一致，容差 0.00003 m。
