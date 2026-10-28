# 计量语义与时空覆盖契约修复：离线交付

**本轮新增真实模型调用 0，新增在线成功 0。** 最终可运行契约为 **`3.0.0-exp2`**，提示词 **`driving_v3_exp2`**，覆盖剖面 **`whole_interval_sources_v3_exp2`**；三者独立记录。已完成实际实现、历史拒绝谓词复查、独立回归和写入保护，不预设在线成功率提高。

最终分析源码冻结于 **`4330487b21bf49c2f09cece3f3cf771fbb821d92`**，本轮开始基线为 `813218abbff4ba87b1cfaf51a09cbc586cce7c04`。[最终离线汇总](offline_final/summary.json) 与 [全部分析源码 SHA](offline_final/source_manifest.json) 对应实际运行源码。旧实验、旧 Schema/提示词、旧响应/配置/失败/接受器/manifest 均未改写，旧 T1 主体 AST 与基线完全相同。

## 实际实现及新旧边界

[中文版契约说明](../../docs/计量与时空覆盖契约3_0_exp2.md)、[新 Schema](../../schemas/agent_output_v3_exp2.schema.json)、[版本化提示词](../../prompts/driving_v3_exp2.txt) 定义全部字段及限制；[原生校验与降低实现](../../agentic/metric_contract.py) 不调用模型、感知或目标预测器。

| 项目 | 旧 2.0/2.1 | 最终 exp2 |
|---|---|---|
| 选择 | 原默认不变 | 必须显式选新输出/提示版本；旧响应不能改标签后通过 |
| 坐标/自车点 | 保持既有字段含义 | 明确固定自车中心参考轴，后轴到中心使用原 d；nuScenes 原 1.2m 为配置假设，不能平移目标或事后换偏移 |
| 目标计量 | 历史身体点、绝对/相对速度定义不足 | annotation box 中心地面 XY；绝对地面速度旋转到固定参考轴，不减 ego |
| 身体状态 | 有包络时可缺位置/速度 | 本实验要求原生参考位置及有向 BODY；速度可 null，不补零。要求更严格，可能增加拒绝 |
| 采集与状态 | 每对象须引用精确参考采集时刻 | 实际图像配对与 state_time_id 分开；异步须原生运动界、条件前提及残差，不添加未实见对象的参考帧 |
| 尺度 | 旧 guard 不变 | 增加可追溯 metric_scale_support；目前只支持基座条件尺寸先验路径，无可见尺寸锚点继续拒绝 |
| 未知/情景 | 原 bool/null 和 alternatives | 类型化原因、对象/空间/时域绑定、情景前提；不启用未知相关性裁剪 |
| 占用 | 固定网格整区间数组 | 支持粗整区间来源，逐模式无正时间缺口，闭边界保留两侧集合及全部来源 |
| 物理链路 | 原自行车、圆包络、安全间距及优化/核验 | 显式降低到相同物理表示；新增完整原输出/source trace，并由 T4 重建核对后独立查碰撞 |

新增尺度支持是模型提出的可证伪条件尺寸先验：锚点须来自实际引用的目标，尺寸与其原生 BODY 一致，附证据、前提及解释。它不认证单目绝对尺度已可靠恢复，工具不做未验证投影。没有锚点时不能凭空加对象、把道路宽当实测值或换尺度标签。自由文本非空也不能替代结构绑定和几何/时间检查；语义真实性仍待独立评价。

源几何已包含 BODY 和情景内运动范围。对齐残差与源几何残差相加后，必须由总 object uncertainty 包住；物理工具仅膨胀总半径一次，不重复加身体/安全间距。名义 BODY 冲突不能靠大残差补救。十进制边界避免 `.1+.2=.3` 的浮点误拒，1e-12 秒正缺口或残差欠额仍拒绝。

开发中间稿 exp1 在 `47b076b` 上冻结，输出保留在 [offline_v1](offline_v1/summary.json)，源码保存在 [development_exp1_source.zip](development_exp1_source.zip)。其 Schema、提示词和文档原文件逐字保留；当前运行时明确拒绝该未发布草稿，不静默按 exp2 解释。exp1 的 233 项测试记录保留在 tests.txt；最终验收采用下面的 exp2 证据，两者不混计。

## 实际验证证据

[完整测试日志](verification_final/unittest.txt)：**244 项全部通过，0 跳过，17.481s**；包含原有 149 项和本轮新增 95 项。运行时禁止网络 connect/connect_ex/create_connection，[来源记录](verification_final/provenance.json) 绑定最终提交及测试/实现/Schema/提示词 SHA。HTTP 适配器仅用进程内 mock 检查契约传递，明确 mock_transport，不能被 T1 当作真实模型来源。

新增验证覆盖合法异步与原生参考状态、陈旧/假引用/ego-only/缺运动界/错误速度语义、粗包络无损细化与闭边界、null/微小缺段/瞬时/占位/缺尺度/漏模式、前提空间与时间、残差组合、车身点/1.2m偏移、T4 原 trace 重建、防摘要重算篡改与防版本降级，以及新旧选择和输出保护。

| 独立人工集成案例 | 实际工具结果 | 执行 |
|---|---|---|
| [6m/s 移动案例](offline_final/authored_moving.json) | T1、T2、真实 CasADi/IPOPT、独立 T4 均通过 | 仅模拟消费 0.2s；位移约1.14m、末速约5.40m/s |
| [静止等待案例](offline_final/authored_stationary.json) | 同链路通过 | 模拟静止短前缀 |
| [未来目标侵入静止自车](offline_final/authored_future_intrusion.json) | T1 保留不利模式；T2 拒绝；独立 T4 检出 BODY_COLLISION | released_prefix_s=0，无执行 |

全部为 `synthetic_fixture`，没有真实图像、真实模型或模拟器隐藏真值。它们只证明契约/软件和所声明假设下的物理检查，不能计入在线成功。

## 27 份历史响应：只复查拒绝

[逐响应、逐字段差异报告](offline_final/historical_predicate_comparison.json) 保留 27 份原始路径/SHA、实际首拒、422 项原失败谓词、原值、证据和当前 guard 位置。方法是旧构造主体 AST 相同、依赖 guard 原字节相同、原输入不变，再以旧显式剖面实际重放首拒；没有填值走到后续 guard，也没有重新计算旧接受器。

- 实际首拒仍为 MISSING_SCALE 11、UNBOUNDED_UNKNOWN 16，27 份全部拒绝。
- 原 422 个失败谓词均仍存在；没有把潜在错误数算成额外历史失败次数。
- 只有六个唯一观测窗口，原四窗与探索两窗保留；另一次服务无响应仍单列。没有合并修订、增加分母或把人工测试计入在线结果。
- 旧原文直接交新 Schema 会拒绝；模型输出字段转换为零，不把 exp2 人工正例归因于历史实验或此前 T3 修复。

## G2 整理：拒绝不可比项

[严格入口与 GT 规范](../../docs/G2_计量语义与证据写保护.md) 检查身体点、速度定义、采集 epoch、图像 SHA、1.2m偏移和共同评价域；错误或未声明时指标为 null，并给原因。相邻约2Hz注释中心斜率不冒充参考时刻瞬时速度。局部 road patch 不与整幅 drivable-area 混算成路缘准确率。

[历史严格入口检查](offline_final/historical_g2_strict_refusals.json) 保留 27 响应、六窗口、170 声明对象行：位置有值51/170、缺失119/170（70%）；速度有值19/170、缺失151/170（88.82%）；原非盲视觉身份关联51/170、未匹配119/170（70%）。重复对象行不是独立目标数，也不是检测召回分母。

旧计量及评价域定义不足，严格入口位置、速度评分均为 **0 项**；这不是零误差、G2失败率或新的通过结论。原有假设下位置/速度/道路及8条未来 surrogate 探索对照全部保留在旧报告，不改数字、不追写为确认性准确率，也不按新剖面复写旧包络。

为保留合法身份关联，做了且仅做了显式离线 GT/审阅表格式桥接：[401条逐字段映射](offline_final/legacy_gt_identity_bridge.json)，将 instance_token 别名为 GT id，并保留原 unique visual review 的证据。没有给旧模型或GT补计量定义、状态时刻、数值或完整覆盖保证，没有最小残差配对；原 GT/关联文件原字节不变。

未来评价 API 将覆盖、面积膨胀和规划可用性分列；未提供绑定工具结果为 NOT_EVALUATED，提供 T1/T2 时只报告已绑定的工具证据，仍不声称独立 T4 或执行成立。宽包络高覆盖不能推出可规划或可靠预测。原生情景源可分别覆盖同一时域，各模式都必须无缝；GT未来代理量也明确标记，不称实测连续真值。GT 始终只在离线评价读取。

## 仍需基座模型和独立数据补齐

[机器清单](model_information_requirements.json) 区分原有缺失和新字段要求：尺度支持与身体尺寸、原生参考位置、实际采集引用与运动界、绝对速度语义、每个合理不利情景的全时域 BODY 包络、残差与前提/未知的绑定。原147条占用 null、全局未知和未知交通状态不能由格式修复消除。模型必须原生提出这些假设，不能从历史文本自动解析后宣称补齐。

新提示词能否被真实服务接受、模型能否给出有依据且稳定的原生输出、尺度/状态/未来模式是否符合真实世界、新剖面在线完成率均**尚未验证**。新 BODY/尺度锚点要求还可能增加拒绝。不具备瞬时速度真值或对应道路边界时，继续只交数据需求，不产生准确率或PASS。

## 写保护、来源与复现

[实际写保护探针](guard_probes.json) 验证已有结果、已有空目录、封存目录中新路径、旧 finalizer 均停止；原 T1 诊断目录92份文件逐字不变。旧 finalizer 作为归档入口直接拒绝，旧字段诊断/反例复跑也只写新目录，内存读取归档源码避免向封存树生成字节码。

开始登记 3,222 份原文件，包括此前跟踪文件、登记外部图像和非工具 runs JSON。完整结果见 [交付保护核对](delivery_protection.json) 与 [逐文件封存](DELIVERY_SHA256_MANIFEST.json)，不是全文件系统监控。原源代码字节另存 [baseline_source.zip](baseline_source.zip)。初次文件哈希在并行授权编辑开始后完成，CLI和旧G2脚本的当时读值已含编辑；[两项来源差异](baseline_source_discrepancies.json) 保留原观察值，并明确以基线 Git blob/ZIP 为修改前源码依据，不将初次读值冒称编辑前值。实验数据/协议保护不依赖这两个可修改入口脚本。

以下命令均写**尚不存在的新目录**，不得改成原 g2 或本封存目录内路径；目标已存在则停止。没有真实模型命令：

```bash
cd /data2/hzy/MAP/agentic-driving

# 全部离线测试；网络连接禁止，输出路径必须全新。
.venv/bin/python implementation/experiments/run_metric_offline_tests.py \
  --out /tmp/new_metric_tests_20261028_A

# 27份历史谓词复查 + 人工T1–T4/模拟前缀 + 严格G2拒评分记录。
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python implementation/experiments/metric_temporal_offline.py \
  --out /tmp/new_metric_contract_20261028_A

# 对已有原生真实记录和独立GT做严格G2；本轮没有此类新真实记录。
PYTHONPATH="$PWD/implementation" .venv/bin/python -m agentic.g2_metric \
  --records /path/to/read_only/native_record.json \
  --truth /path/to/read_only/metric_truth.json \
  --config /path/to/read_only/evaluation_domain.json \
  --out /tmp/new_strict_g2_20261028_A
```

源代码自检要求实现/测试/Schema/提示/文档与当前 Git 提交一致；数据与日志新建引起的工作树 dirty 如实记录。旧报告中的刷新命令属于历史文档，不再用于当前复现；新入口会阻止覆盖。

## 后续真实实验仍未启动

[实验提案](next_experiment_proposal.json) 为 `launched=false`：六个原唯一窗口，两臂分别为旧2.1/driving_v2_3和原生exp2/driving_v3_exp2，原四窗与探索两窗分开，至多24调用、每试验最多一次语义修订、无自动传输重试，调用600s、总wall18000s。失败计预算，服务/认证/模型身份或原字节异常立即停止；不更换窗口、不挪预算、不追加批次。

它比较的是“契约+提示”的一包变化，不能当作单因素提示因果对照。成功仍须真实模型来源、T1–T4、独立接受器及短前缀回执全部成立，区分移动/静止、首次/修订后；GT只在结果封存后评价，数值通过阈值尚未设定。未经单独确认不执行。

封存阶段的一次失败与初次修复检查保留在 [seal_attempt_failure.json](seal_attempt_failure.json)：开发 ZIP 的前缀筛选漏入了 Git 转义的中文文档路径。原 ZIP 未改；[补充归档](development_exp1_docs_supplement.zip) 与 [逐文件来源](development_exp1_docs_supplement.json) 从47b076b恢复11份文档原字节，两项新增回归实查其可恢复性，最终全套244项通过。这是封存工程问题，不混称模型或物理工具失败。此前exp2的242项及中间离线输出原样保留，最终依据为offline_final/verification_final。
