# 固定真实输出与新增在线结果对照

本表为零调用读后汇总；每行取该run最后一份有效原生响应，全部初答/修订/服务失败和候选逐项保留在 comparison.json。旧接受器内部ONLINE键不改变离线回放来源。

|记录|来源|有效输出/扣账|最后有效响应的结果|候选 T2/T3/T4通过数|消费/位移m/末速m/s|最小条件间距m|最大加速度/离散jerk代理/最大转角|
|---|---|---|---|---|---|---|---|
|公交旧在线|真实在线|2/2|CONDITIONAL_CHAIN_PASS|-|MOVING_PREFIX/1.2719/6.3208|1.7472|0.3855/0.1610/0.0000|
|停车旧在线|真实在线|2/3|T2:CORRIDOR_SEED_BLOCKED[0.0, 0.2]；终止服务错误 CODEX_SERVICE_FAILURE|-|NOT_EXECUTED/-/-|-|-/-/-|
|施工旧在线|真实在线|3/3|T2:CORRIDOR_EMPTY[0.0, 0.2]|-|NOT_EXECUTED/-/-|-|-/-/-|
|停车旧BOX回放|真实输出离线重规划|1/0|CONDITIONAL_CHAIN_PASS|-|MOVING_PREFIX/1.0326/5.1106|2.2979|0.5216/0.2178/0.0000|
|施工旧BOX回放r2|真实输出离线重规划|1/0|T2:CORRIDOR_EMPTY[0.0, 0.2]|-|NOT_EXECUTED/-/-|-|-/-/-|
|施工旧BOX回放r3|真实输出离线重规划|1/0|T2:CORRIDOR_EMPTY[0.2, 0.4]|-|NOT_EXECUTED/-/-|-|-/-/-|
|bus iteration_1五候选回放|真实输出离线重规划|1/0|CONDITIONAL_CHAIN_PASS|2/2/2 (共5)|MOVING_PREFIX/1.2719/6.3208|4.4611|0.3855/0.1610/0.0000|
|parking iteration_1五候选回放|真实输出离线重规划|1/0|CONDITIONAL_CHAIN_PASS|2/1/1 (共5)|MOVING_PREFIX/1.0326/5.1106|2.2979|0.5216/0.2178/0.0000|
|construction iteration_1五候选回放|真实输出离线重规划|1/0|T2:CORRIDOR_SEED_OUTSIDE[0.8, 1.0]|0/0/0 (共5)|NOT_EXECUTED/-/-|-|-/-/-|
|construction iteration_2五候选回放|真实输出离线重规划|1/0|T3:OPTIMIZATION_FAILED|2/0/0 (共5)|NOT_EXECUTED/-/-|-|-/-/-|
|construction 本轮新在线|真实在线|1/2|T2:CORRIDOR_SEED_OUTSIDE[0.8, 1.0]；终止服务错误 CODEX_SERVICE_FAILURE|0/0/0 (共5)|NOT_EXECUTED/-/-|-|-/-/-|
|redlight 本轮新在线|未调用|0/0|NOT_ATTEMPTED|-|NOT_EXECUTED/-/-|-|-/-/-|
|bus 冻结最终源码回放|真实输出离线重规划|1/0|CONDITIONAL_CHAIN_PASS|2/2/2 (共5)|MOVING_PREFIX/1.2719/6.3208|4.4611|0.3855/0.1610/0.0000|
|parking 冻结最终源码回放|真实输出离线重规划|1/0|CONDITIONAL_CHAIN_PASS|4/1/1 (共5)|MOVING_PREFIX/1.0326/5.1106|2.2979|0.5216/0.2178/0.0000|
|construction 冻结最终源码回放|真实输出离线重规划|1/0|T3:OPTIMIZATION_FAILED|2/0/0 (共5)|NOT_EXECUTED/-/-|-|-/-/-|
|live_construction 冻结最终源码回放|真实输出离线重规划|1/0|T3:OPTIMIZATION_FAILED|2/0/0 (共5)|NOT_EXECUTED/-/-|-|-/-/-|
|施工新JSON→旧BOX回放|真实输出离线重规划|1/0|T2:CORRIDOR_EMPTY[0.2, 0.4]|-|NOT_EXECUTED/-/-|-|-/-/-|

同一模型输出的T1场景对照：全部已到达项逐值及规范化摘要相同。
位置、道路、BODY、全部情景、残差和1m安全间距均由完整T1摘要保护；源文件SHA另列。
加速度单位m/s²、离散jerk代理m/s³、转角rad；无轨迹时留空。连续控制在网格处跳变，因此不能将差分代理解释成连续RMS jerk。

登记4个唯一窗口，有模型输出3个；本轮新增在线请求涉及1个窗口。重复回放及修订不增加窗口数。
圆盘与连续车身核验的间距下界采用不同保守包络，数值增大不能解释成实测真实安全改善。
完整独立窗口列表、每次工具/服务阶段、候选失败时间与原始失败对象均见 comparison.json；不合并计算成功率，不作G2或真车安全结论。
