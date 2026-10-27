# Agentic Semantic-to-Physical Driving V1.0：接口与端到端测试包

交付日期：2026-10-27。数据契约版本：`1.0.0`。实现剖面：**直道、轴对齐矩形、停车让行与直道起步**。

本包把方案中的四个工具落实为严格 JSON Schema、中文接口说明、可执行 Python 参考实现与回归测试。模型仍负责每轮驾驶决策和全部环境假设；工具不调用检测、跟踪、深度估计或目标预测模型。

## 文件入口

| 交付 | 文件 |
|---|---|
| T1 米制场景构造接口 | `docs/T1_米制场景构造.md`、`schemas/T1.schema.json` |
| T2 时空走廊构建接口 | `docs/T2_时空走廊构建.md`、`schemas/T2.schema.json` |
| T3 语义条件轨迹优化接口 | `docs/T3_语义条件轨迹优化.md`、`schemas/T3.schema.json` |
| T4 轨迹核验接口 | `docs/T4_轨迹核验.md`、`schemas/T4.schema.json` |
| 共享场景数据格式 | `docs/共享场景数据格式.md`、`schemas/shared_scene.schema.json` |
| 行人横穿案例及预期行为 | `docs/行人横穿端到端案例.md` |
| 初始输入与新观测 | `examples/pedestrian_crossing.request.json`、`examples/pedestrian_clear_resume.request.json` |
| 工具参考实现 | `driving_tools.py` |
| 一键运行 | `run_demo.py` |
| 自动化测试 | `tests/test_pipeline.py` |
| 实测结果与完整消息 | `results/summary.json`、`results/*.trace.json` |
| 验证报告 | `results/验证报告.md`、`results/test_results.txt` |

## 运行

要求 Python 3.10 或更高版本。唯一外部依赖为 `jsonschema`。先进入本目录，再执行：

```powershell
python -m pip install -r requirements.txt
python run_demo.py
python -m unittest discover -s tests -v
```

如果当前 Python 已安装兼容依赖，可以直接运行后两条命令。本次在 Python 3.11.5、jsonschema 4.17.3 环境完成验证，最终环境信息见验证报告。Schema 以 Draft-07 编写，使用包内注册表离线解析，不访问 `.local` 域名。

## 一次调用

```python
import json
from pathlib import Path
from driving_tools import call_tool

request = json.loads(Path("examples/pedestrian_crossing.request.json").read_text(encoding="utf-8"))
response = call_tool(request, allow_synthetic=True)
assert response["status"] == "ok"
scene = response["payload"]["scene"]
```

实际模型调用适配器不在本包中；它应把模型结构化输出组装成 T1 请求。模型输出与采集元数据的来源标记由可信调用端填写，工具不能验证一个字符串是否代表真实的模型调用。

## 案例结果

1. 正常让行：前进 12 米处停车；T4 通过，允许执行前 0.2 秒。
2. 故障注入：人为将 T3 输出替换成匀速前进轨迹；T4 检出间距不足、越过停车边界和未按时停车，释放执行时长为零。
3. 反馈重规划：脚本模拟智能体把停车参考点上界改为 10 米；重新运行四个工具，在 9 米处停车并通过核验。
4. 新观测起步：正常停车分支在参考时刻 104 秒获得新的人工观测；行人已离开车道，脚本模拟智能体发出起步决策。重新运行四个工具，5 秒后速度为 2.5 米/秒。

完整请求与响应均保存，便于对照接口与回放。

## 已实现与待实现的边界

| 项目 | 本包状态 |
|---|---|
| 严格接口、版本对齐、证据引用、占用时域检查 | 已实现 |
| 轴对齐矩形占用、未知区域阻塞、车身尺寸处理 | 已实现 |
| 纵向候选轨迹求解与独立核验 | 已实现 |
| 正常、故障、反馈修订、新观测起步 | 已实现，均为人工测试替身 |
| 在线多模态模型与真实图像接入 | 未实现；示例没有真实图片，`fixture://` 为测试引用 |
| 图像到米制投影、异时动态对齐、多假设融合 | 未实现；当前只接收已在参考时刻自车坐标系下的米制假设 |
| 任意多边形、转弯、变道、自行车模型非线性优化 | 未实现；后续扩展，不得将本求解器描述成 CasADi/IPOPT 实现 |
| G2 模型米制 grounding 可行性验证 | 未完成；测试通过只证明该人工输入下程序行为符合契约 |
| 车辆控制器或实时执行许可服务 | 未实现；输出执行前缀仅是离线数据，不会控制车辆 |

运行时默认拒绝 `synthetic_fixture`；测试必须显式传入 `allow_synthetic=True`。不包含 CALL/SKIP 门控，不会在工具失败时切换到其他驾驶模型。执行端还需在当前采集时钟下确认数据未过期，不能直接执行本离线示例。

后续扩展复杂几何和运动模型时，应升级剖面/契约版本，新增实现与测试；不得将当前轴对齐矩形中的字段静默解释成一般多边形或未知运动边界。
