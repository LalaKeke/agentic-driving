# frame005 单帧恢复结果

**一次真实OpenAI API生成，全链及0.2秒移动转弯前缀通过。** 原预检失败保留，本次新建独立补跑；前五帧没有重跑。所有调用已停止。

固定原样本 `b4ff30109dd14c89b24789dc5713cf8c`，10张图、采集及SHA、`driving_prototype_v3`、3.0.0-exp2、2秒/.2秒、原车辆/1米间距与 `bounded_seed_candidates_v2` 均未改变。

|环节|实际结果与证据|
|---|---|
|连接定位|旧记录为非生成/models预检ConnectError，未进入模型或T1–T4。当前[无认证路由检查](connection_check.json)返回HTTP401/TLS1.3；[同client认证预检](runtime/frame_005/transport/network_preflight.json)返回HTTP200、原模型可用|
|真实模型|1次POST、无自动重试、HTTP200；[原生JSON](runtime/frame_005/transport/attempt_01/native_output.json)/[原文](runtime/frame_005/transport/attempt_01/final_agent_message.txt)，通过原业务Schema|
|T1|[米制场景](runtime/frame_005/attempt_01/T1.json)，PASS|
|T2|[候选走廊](runtime/frame_005/attempt_01/T2.json)，PASS|
|T3|[真实IPOPT轨迹](runtime/frame_005/attempt_01/T3.json)，Solve_Succeeded|
|T4|[独立核验](runtime/frame_005/attempt_01/T4.json)，PASS、违例0，最小条件间距下界3.258330m|
|独立接受器|[重建/核验/来源与积分](runtime/frame_005/attempt_01/acceptor.json)，PASS|
|模拟消费|[状态](runtime/frame_005/attempt_01/execution.json)/[回执](runtime/frame_005/attempt_01/execution_receipt.json)：0.2秒、位移0.703780m、末速3.490329m/s、转角1.859779°，MOVING_PREFIX|

2秒计划末航向变化9.505631°、横向位移0.948322m；这段长尾没有执行。[逐层账本](runtime/frame_005/attempt_01/stages.json)、[实际批次计数](batch_summary.json)保留。服务返回模型gpt-6.1-sol，独立快照未知。未调用额外模型、未读GT、未改变模型几何/残差或规划物理边界。

**本次仅修复了这帧缺失的运行结果。** 无需修改API适配器或T1–T4；当前网络已恢复，代理/上游间歇断连的永久根因仍未确认。旧失败不追写为成功，新汇总用“预检失败后首次生成通过”明确区分。

[更新后的六时刻总览、动画和PDF](../api_continuous_bend_20261029_v3/README.md)保留原先全部失败；单帧[零调用复算](offline_replay/summary.json)已实际通过，不增加在线分子。源码e00fca9、注册eda3f7e；本轮仅修改合并展示中预检失败后的状态名称，3项既有API计数回归通过；原417项工程验证仍为此前证据。
