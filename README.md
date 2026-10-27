# Agentic Driving

Agentic Semantic-to-Physical Driving V1.0：由多模态驾驶智能体持续输出驾驶决策和环境假设，四个确定性工具将其转化为可核验的物理轨迹。

## 工具链

| 工具 | 职责 |
|---|---|
| T1 米制场景构造 | 校验和整理模型场景假设，统一场景结构与来源 |
| T2 时空走廊构建 | 根据场景、车辆尺寸与决策构造时变可行区域 |
| T3 语义条件轨迹优化 | 在行为和运动约束内生成候选轨迹 |
| T4 轨迹核验 | 独立检查轨迹，返回通过结果或具体违例 |

环境信息只来自基座模型；允许确定性相机标定、采集时间戳与自车状态。不接入其他感知或预测模型，无 CALL/SKIP 门控。

## 项目结构

```text
implementation/       四工具接口、共享场景格式、参考代码、样例与测试
docs/reports/         A4 汇报方案
paper/                研究参考论文
.github/workflows/    GitHub Actions 自动测试
```

`paper_audit/` 是本地论文审阅过程生成的中间文件，已从 Git 跟踪范围排除。原始研究草稿保留在仓库根目录。

## 开始运行

要求 Python 3.10 或更高版本，自动测试使用 Python 3.11。

```shell
cd implementation
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python run_demo.py
```

详细字段、四份工具接口和结果日志见 [实现包说明](implementation/README.md)。完整案例覆盖停车让行、故障拒绝、反馈重规划与新观测起步。

## 当前阶段

已实现严格 JSON Schema 与直道矩形场景的纵向候选求解、独立核验。测试使用人工场景及智能体脚本替身，没有真实图像处理、在线模型调用或车辆控制。尚未完成 G2 模型米制 grounding 可行性验证，也未实现任意道路几何或一般非线性轨迹优化。

## 开发管理

- `main` 保存已通过测试的版本；功能开发使用独立分支。
- 提交前运行回归测试和案例；推送和 Pull Request 会触发同样的自动测试。
- 用 Issues 记录任务、实验设计与问题；建议首先跟踪 T1 模型适配、G2 验证、一般几何和非线性轨迹优化。
- API 密钥只放在本地环境变量或被忽略的 `.env` 文件中。
