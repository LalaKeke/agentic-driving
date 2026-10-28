# Agentic Semantic-to-Physical Driving V1.0 可运行研究实现

[案例、四工具输入输出与轨迹叠加](results/casebook/index.html)：官方 nuScenes 相机渲染、真实拒绝链路、人工停车/恢复完整链路，附十三页 PDF。

实验可视化：[离线交互报告](results/visual_report/index.html)，含真实图像与工具拒绝链路、G2 点位对比及单独标注的人工轨迹回放。

新增独立 v2 多边形/自行车优化剖面，保留原 v1 直道矩形枚举基线和全部原测试。此项目是以模型场景假设为条件的研究工具链，不是完整视觉驾驶系统、通用全局轨迹优化器或真实车辆控制器。

## 本机安装与运行

实际工作目录 `/data2/hzy/MAP/agentic-driving`；新建独立 `.venv`。本机 Python 3.11.5、96 CPU、251 GiB 内存；未发现 Slurm/PBS 命令，GPU 驱动不可用，因此直接执行 CPU 测试。不采用仓库 hpc 中旧服务器的 SSH、账号、路径或调度配置。

```bash
cd /data2/hzy/MAP/agentic-driving
python -m venv .venv
.venv/bin/python -m pip install -r implementation/requirements.lock.txt
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1

# 1. 全部离线测试（包含旧基线）
.venv/bin/python -m unittest discover -s implementation/tests -v

cd implementation
# 2. 人工行人横穿完整闭环：故障拒绝、反馈修订、短前缀、完全停车、新观测恢复
../.venv/bin/python -m agentic.cli demo --runs ../runs

# 保留的旧纵向枚举演示；会刷新旧 results，非新优化器
# ../.venv/bin/python run_demo.py

# 离线配对回放：全部数据是显式人工测试替身
../.venv/bin/python -m agentic.cli replay \
  --captures examples/v2/artificial_capture.json \
  --outputs examples/v2/artificial_output.json --allow-synthetic --runs ../runs

# 3. 对指定多帧图像数据调用真实模型：先从 .env.example 安全导出环境变量
# capture JSON 的 observations[].path 指向实际 PNG/JPEG/WebP；不是 fixture://
../.venv/bin/python -m agentic.cli online \
  --capture /path/to/real-capture.json --sample-id sample01 --calls 3 --runs ../runs
# 加 --plan：每次观测调用模型、四工具、失败修订与模拟短前缀执行

# 4. G2 独立离线评价；真值永不传入运行时
../.venv/bin/python -m agentic.cli g2 \
  --records ../runs/<run-id>/*-model-record.json \
  --truth /separate/real-truth.json --config examples/v2/g2_config.json \
  --out /tmp/g2-result.json
```

`.env.example` 只是配置说明，不自动加载。服务必须实际支持 Chat Completions 图像输入，并选择其支持的 json_schema/json_object/prompt_json 输出模式；不预设具体模型存在，不自动降级接口、换模型或松弛约束。认证、HTTP 响应原文、请求头和图像字节不写日志；HTTP/超时错误被脱敏。真实模型输入格式参考 capture_v2.schema.json；人工 capture 仅供结构参考，其 fixture:// 不是图片。

## 模块与边界

- `agentic/adapter.py`、`prompts/driving_v2.txt`：真实多模态 HTTP 调用，有限重试、超时、严格校验、来源记录；每轮均参与决策。
- `agentic/t1.py`：证据、时间、单位、尺度、版本、占用覆盖及假设冲突校验，保留备选占用；未知不填零。
- `agentic/t2.py`：凸道路/区域、目标整区间多边形占用、车辆外接圆、固定分离半平面的保守时空走廊。
- `agentic/t3.py`：实际 CasADi 3.7.2/IPOPT 多重射击，后轴运动学自行车，恒加速度/曲率解析区间运动及连续走廊约束。
- `agentic/t4.py`：独立原始场景核验、连续时间保守车身扫掠、采样间停车期限检查、具体违例、绑定轨迹的短前缀。
- `agentic/tools.py`：四个独立 JSON 工具接口，区分协议/不可行/核验拒绝。
- `agentic/runtime.py`：有限失败反馈修订、每轮重观测、一次性短前缀模拟执行、失效检查和按运行编号日志。没有真实控制接口。
- `agentic/fixtures.py`：人工测试智能体，约 6 m/s 自车、前方 18m 行人；使用已公开的自车前缀位移重置坐标、按采集时刻提供人工占用，无真实图片。模拟执行器不包含行人/道路隐藏世界。
- `agentic/g2.py`：仅离线导入真值的指标计算入口。已对真实 nuScenes 模型输出进行部分 G2 评价；单元测试的虚构数值不计入实验。

完整契约、几何保守性、行为模板和迁移限制见 [v2 接口与迁移](docs/V2_接口与迁移.md)。G2 数据格式及评价约定见 [G2 实验入口](docs/G2_实验入口.md)。实测证据见 `results/v2/`，完整每轮日志在根目录被忽略的 `runs/<run-id>/`。

## 尚未验证 / 未实现

已接入本服务器 nuScenes 的四个真实样例及隔离离线标注/地图，运行方法见 [nuScenes 样例说明](experiments/nuscenes/README.md)。官方 Codex CLI 的 ChatGPT 会员适配已实际跑通 gpt-6.1-sol，多帧图像有效响应共 10 份（原契约 9 份、独立 2.1 夜间格式复核 1 份）。真实模型场景均未满足尺度/未来占用要求，没有释放执行前缀；速度和可支持的完整未来覆盖无可用得分，G2 不判通过。详见 [真实调用与 G2 报告](results/gpt61_live/README.md)。项目内固定客户端 0.164.0，系统 CLI 未修改。离散标注插值不代表已知真实连续时间占用。没有接入额外感知或预测模型。仅支持原生米制模型假设与凸道路/目标区域；标定投影辅助函数不等于已经完成视觉米制 grounding。车身外接圆和固定半平面会保守拒绝窄路、非凸道路、复杂绕障；IPOPT 是局部约束求解，无全局最优保证。无轮胎动力学、硬 jerk/转向速率约束、完整交通规则解释器、相机驱动或真实车辆控制。所有核验结论均依赖模型场景和整区间覆盖假设。

## 核对的官方资料

2026-10-27 阅读 [OpenAI 图像输入](https://developers.openai.com/api/docs/guides/images-vision?api-mode=chat)、[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) 后实现显式能力模式及本地校验；是否支持具体模式需用所选服务/模型实际验证。根据 [CasADi 官方文档](https://web.casadi.org/docs/) 的 Opti/NLP/IPOPT 接口实现并在本机实际求解。依赖版本由 requirements.lock.txt 记录。
