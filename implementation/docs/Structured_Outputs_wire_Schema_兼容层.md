# Structured Outputs：独立 wire Schema 最小兼容层

本层只处理传输表达，业务输出契约仍为 `3.0.0-exp2`，提示词仍为 `driving_v3_exp2`，覆盖剖面仍为 `whole_interval_sources_v3_exp2`。原业务 Schema、提示词、旧 pilot 及其失败原文保持原字节。旧 pilot 已结束、预算使用 1/2、原生输出 0；剩余一次不转移、不自动消费。

## 产物与版本

`agentic/wire_schema.py` 从业务 Schema 的确切 UTF-8 字节生成独立 `wire_schema.json`，生成器版本 `1.0.0`，wire 版本 `so_const_type_v1`。`transformation_manifest.json` 分别记录业务原字节 SHA、wire 序列化字节 SHA、规范化摘要及每处 JSON pointer、原节点、是否存在原值、新节点、const 原值与等价依据。SHA 的用途不能互换。

当前业务 Schema 只有十二处变换，全部为 string const 的冗余 `type: "string"`；没有 enum 类型推断或其他变换。wire 文件仍保留原 `$id`，因为它描述相同业务实例集合；wire 的独立版本由旁置 manifest 和运行来源记录区分，不改业务身份。

## 有限的等价依据

对实际适用节点，原约束是 `const=c` 与原有其他约束的交集；新增约束是 `type=T(c)`。在指定 Draft 7 语义中，等于 c 的 JSON 实例已经具有 T(c)，交集不改变合法实例集合。布尔不同于数值，null 有独立类型；整数 1 和数值 1.0 在 const 比较中相等，整数类型也允许数学上为整数的数值。字面 object/array 仅添加其外层类型，不进入其内部数据。

生成器保守限制为标准 Draft 7 及真正 Schema 节点。它遍历 properties、definitions、组合分支、items 等已知 Schema 位置，跳过 const、enum、default、examples 和其他普通数据。仅从当前节点自身的 const 推导，不从 anyOf 汇总或 `$ref` 猜类型。Draft 7 `$ref` 的同级 const 不用于注记。指向普通数据的引用、外部/anchor 引用、嵌套资源 ID、不支持 dialect、重复键或不能保值往返的数字均输出冲突，停止生成，不产出部分 wire。

已有 type 或其他约束原样保留，即使原约束相互矛盾也不尝试“修好”。默认采用 Draft 7 的情况会在 manifest 中明示。差分正反样例检验是回归证据，不是任意 Schema 的一般等价证明；外部词汇/自定义验证语义也不在保证范围内。

## 三态兼容审查

`agentic/wire_schema_review.py` 逐节点列出 `CONFIRMED_SUPPORTED`、`EXPLICIT_CONFLICT`、`UNCONFIRMED`。其中“支持”仅指当前官方通用 API 文档描述的形式，不能证明某一账户、模型别名、CLI 或后端部署已接受。审查不修改约束，不会删除 minLength、改变 required 或将未知改为空对象。

官方指南对根对象、闭对象、必填与可空、类型、anyOf、本地引用/递归及结构限制有说明；部分关键字的支持还取决于模型类别。当前明确保留 enum-only、minLength、const 具体后端形式及元注记等未确认项。[官方 Structured Outputs 指南](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)

深度给出本地语法路径计数及其算法限制，不能当后端深度计数的认证。固定 CLI 0.164.0 以版本及二进制 SHA 为证；app-server 的通用 JSON `outputSchema` 参数只证明 RPC 可以携带对象，不能证明后端支持任意 JSON Schema。旧 pilot 的确切 missing-type 错误仍是唯一实际服务拒绝证据；本轮不发兼容性请求。

## 运行时边界

`PilotSubscriptionAgent(wire_schema_bundle=...)` 为显式选择；缺省行为仍发送原业务 Schema。加载及每次决定前必须由原业务字节完整重生 wire 并比对 manifest，发现篡改在请求前停止。模型最终文本原样保存，JSON 解析结果单独保存，来源记录独立业务/wire/生成器 SHA 与版本；任何随后失败都不删除原生文本。

之后仍调用原 `validate("agent_output_v3_exp2.schema.json", output)`，再进入原 T1–T4 和独立接受器。不补字段、不做类型强转、不替换 null、不改尺度标签或数值。业务验证、工具、独立接受器与预算实现没有放宽。新元数据也不能把 mock_transport 改成 online_model。

## 离线准备及迁移边界

以下命令不调用模型；输出目录必须尚不存在且不位于封存证据下。分析要求源码与当前本地 Git 提交一致。

```bash
cd /data2/hzy/MAP/agentic-driving
.venv/bin/python implementation/experiments/prepare_exp2_wire_trial.py \
  --out /tmp/new_exp2_wire_preparation_20261028_D
.venv/bin/python implementation/experiments/run_metric_offline_tests.py \
  --out /tmp/new_exp2_wire_tests_20261028_D
```

准备程序校验旧 pilot 封存和固定 CLI 文件，输出业务/wire 三态审查与同一个白天窗口的新 trial 注册。注册 `launched=false`、`execution_authorized=false`、授权调用 0；提议的新预算上限为 2，独立于旧 pilot 未用的一次。运行入口对该注册在创建预算、读取认证或启动 transport 前拒绝。未来需新的明确授权及冻结配置，生成兼容性探测也计入新预算；接口异常立即停止。六窗批次继续不启动。

原业务响应从未转成 wire 响应或重新计为成功。将来若单窗通过，仍只可报告真实图像/真实模型条件式链路和模拟短前缀消费；不能由 Schema 修复推断在线成功率、G2 通过或真实车辆安全。
