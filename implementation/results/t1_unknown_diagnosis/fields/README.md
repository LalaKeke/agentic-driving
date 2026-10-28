# 逐响应、逐字段离线诊断表

`issues.csv` / `issues.jsonl` 保留失败谓词；`checks.jsonl.gz` 包含全部通过、失败、不可评及信息项。每行的 `record.path`、`record.sha256`、`json_pointer`、`raw_value` 可直接定位原始值。配对 capture/feedback、冻结代码、条款原文及提示词位置通过 `reference_catalog.json` 无损解引用；格式见 `format.json`。

`responses.json` 为27份有效响应分别保存实录首拒、独立潜在错误、对象可用性及原始条件说明；`windows.json` 按六个唯一窗口、实验组/提示词和修订分开，修订字段差异不自动建立对象身份对应。它们是诊断分布，不是新增在线成功率。

表内 `recorded_first_rejection` 才是当时实际首拒。`independent_failed_predicate_masked_in_recorded_run` 只表示原值可独立检出的后续问题；`NOT_EVALUABLE` 表示缺少依赖项，不能算额外已证实错误。`T2_DIAGNOSTIC` 单列潜在下游保守性，不是这些在线运行的实际失败原因。未知瞬时位置/速度的 null 单独不触发 T1 拒绝；occupancy=null 不等于空占用。

长表可离线读取：

```python
import gzip, json
from pathlib import Path
root = Path('implementation/results/t1_unknown_diagnosis/fields')
catalog = json.loads((root / 'reference_catalog.json').read_text())
with gzip.open(root / 'checks.jsonl.gz', 'rt') as stream:
    for line in stream:
        row = json.loads(line)
        rule = catalog['rules'].get(row['source_rule_ref'])
        # row['raw_value'] 是原字段值；rule 是冻结源码中的位置与谓词。
```

所有模型、工具和执行调用均为0。没有向缺失值填数，没有用真值，没有改变旧接受器结论。完整输入SHA、冻结源码ZIP复核及旧证据起止字节保护见 `inputs.json`、`frozen_rule_sources.json`、`summary.json`。源码提交和实际脚本SHA记录在 summary；未经确认不启动新模型实验。
