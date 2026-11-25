# CorDriver × Astra · nuScenes 开环驾驶规划

CorDriver 从相机、标定、导航和实测自车信息生成初轨与分时走廊。触发慢系统时，`gpt-6.2-astra` 审阅六张环视图、前视轨迹投影和数值轨迹，返回 `KEEP` 或 `ADJUST`。调整指令给出 1.5 秒、3 秒两个有方向的位置窗和速度范围。快系统在 3 秒内平滑走廊、优化控制并核验整车、道路和预测参与者。独立评价用 nuScenes 的录制未来计算 ACR、CCR 和 L2。

## 从这里开始

| 目的 | 入口 |
|---|---|
| 从原图重跑单帧 | [输入准备、模型回放或真实调用、规划、独立评分](implementation/experiments/CORDRIVER.md) |
| 配置模型和数据 | [环境、上游代码、发布权重、数据文件与 SHA-256](implementation/environment/README.md) |
| 核对冻结对照 | [99 帧快慢系统的选择回放和 119 帧指标](implementation/evaluation/README.md) |
| 比较目标框求解与碰撞收益 | [实际快轨迹审阅、98 帧历史配对与新场景测试](implementation/evaluation/transfer_20261122.md) |
| 重跑 0018/31 路缘修复 | [五次控制求解、原门槛及最终轨迹](implementation/case_study/README.md) |
| 查看前视投影 | [使用本地 nuScenes 图像重画 Astra 案例](implementation/evaluation/README.md#前视图) |

只需 Python 3 标准库即可从干净检出复核结果。第一条从逐帧记录汇总指标，第二条根据冻结触发条件、模型指令、候选和收据重做快系统和双系统的选择。

```bash
python3 -B implementation/evaluation/review.py metrics
python3 -B implementation/evaluation/review.py dispatch
```

## 当前结果

发布版 5,119 个有效起算帧中，119 帧在 3 秒内命中开环碰撞评分器。这些帧中有 99 帧具备因果对照所需的输入。下表仅汇总同输入的 99 帧。

| 方法 | 参与者 ACR 帧数 | 路缘 CCR 帧数 | 任一碰撞帧数 | 三时域平均 L2（m） |
|---|---:|---:|---:|---:|
| 同输入原生 CorDriver | 6 | 57 | 63 | 0.67144 |
| 纯快系统 | 6 | 45 | 51 | 0.67044 |
| Astra 双系统 | 5 | 46 | 51 | 0.66963 |

双系统相对纯快系统消除 2 帧碰撞，同时新引入 2 帧。0018/31 的后续开发修复单独报告。两个结果都限定于当前开环样本。

从原图重跑需要使用者本地的 nuScenes 数据及发布权重。0221/23 可使用已绑定的 Astra 回答复验；新的付费请求由 `plan --live` 显式启用。建议把运行输出放在本地 `outputs/` 目录。推理与评分分别由不同的命令执行。

[2026-11-24 官方全验证集双行测评：5,119 个有效帧，5,118 个实测配对](implementation/evaluation/results/official_val_20261124/RESULTS.md)
