# 各模块参数与实际作用

本表对应跟车场景 `frame_004 / attempt_02`，样本 `747aa46b9a4641fe90db05d97db2acea`。运行实值取自保存的JSON；源码默认值与容差取自该次配置登记、且与当前文件SHA一致的采集器、T1计量契约、T2、T3、T4、执行器和接受器源码。本次只整理文档，没有重新调用模型、求解或执行。

“硬约束”表示必须成立才能释放轨迹；“软目标”影响优化代价；“条件场景参数”是模型假设下的检查依据，不代表真实世界已校准误差界；“数值容差”用于浮点或几何一致性判断，不是额外放宽物理标准的配置开关。

## 1. 当前案例与协议

来源：[原始输入record.json](/data2/hzy/MAP/agentic-driving/implementation/results/long_clips_20261029_v2/following_deceleration/runtime/frame_004/attempt_02/record.json)、[窗口配置](/data2/hzy/MAP/agentic-driving/implementation/results/long_clips_20261029_v2/following_deceleration/runtime/frame_004/config.json)。

|参数|实际值|单位/作用|类别|
|---|---|---|---|
|scene_id|`nusc_747aa46b9a4641fe90db05d97db2acea`|贯穿工具的场景身份|硬接口要求|
|revision|1|本窗初答后的第一次语义修订；不能混用别的轮次|硬接口要求|
|reference_time_id|`nusc_us_1533151605512404`|回指实际采集时刻|硬接口要求|
|reference_time_s|1533151605.512404|秒，绝对采集时间|可信采集输入|
|valid_until_s|1533151607.512404|秒，场景有效截止|硬时效要求|
|history_s|2.0|秒，输入历史窗口|输入组织|
|horizon_s|2.0|秒，本次规划时域|硬协议要求|
|step_s|0.2|秒，10个区间、11个轨迹状态|硬协议要求|
|safety.prefix_s|0.2|秒，核验通过后可模拟消费的短前缀|硬执行上限|
|output contract|`3.0.0-exp2`|模型原生输出契约|硬接口要求|
|coverage profile|`whole_interval_sources_v3_exp2`|整时间区间BODY源，允许有依据的粗分段|硬覆盖语义|
|prompt version|`driving_prototype_v3`|实际模型固定提示词版本|输入配置|
|experiment profile|`local_conditional_2s_prototype_v1`|局部条件式原型，非旧严格五秒实验|结论适用范围|
|search profile|`bounded_seed_candidates_v2`|5个固定自车参考候选|算法选择|
|planner / verifier|`yaw_bounded_box_v1` / `continuous_oriented_body_v1`|有向完整车身规划与连续核验|算法选择|
|require_turn|false|本例不强迫满足独立的转弯幅度展示门槛|任务配置|

采集容器的 `metadata.profile=convex_disk_bicycle_v2`、`schema_version=2.0.0` 是兼容保留字段，**不能据此判断本例仍采用圆盘规划**；本例实际算法由上面的独立planner/search/verifier字段声明。

## 2. 计量、自车、车辆与安全参数

来源：[计量定义](/data2/hzy/MAP/agentic-driving/implementation/agentic/metric_contract.py:14)、[中心/后轴转换](/data2/hzy/MAP/agentic-driving/implementation/agentic/geometry.py:25)。

|参数|实际值|单位/作用|类别|
|---|---|---|---|
|coordinate_frame|`ego_at_reference`|原点为参考时刻车身中心；X前、Y左，坐标轴固定|硬计量语义|
|目标位置参考点|BODY包围盒几何中心的地面XY|Schema常量名为 `annotation_box_center_ground_xy`，仅约定身体点，不意味着运行时读取GT注释|硬计量语义|
|目标速度|绝对地面速度在参考固定轴上的分量|不能事后切换成相对自车速度|硬计量语义|
|ego.x_m / y_m / yaw_rad|0 / 0 / 0|m / m / rad，已重置到参考自车中心|初态硬约束|
|ego.speed_mps|8.348395306568168|m/s，来自因果后向定位差分|初态硬约束|
|vehicle.front_m / rear_m|2.3 / 2.3|m，中心至前/后端，全长4.6m|车身几何|
|vehicle.half_width_m|0.9|m，全宽1.8m|车身几何|
|center_to_rear_axle_m|1.2|m，中心在后轴前方；初始后轴为[-1.2,0]m|参考点转换|
|wheelbase_m|2.7|m，自行车模型轴距|动力学硬参数|
|max_speed_mps|12|m/s，实际约束0≤v≤12|硬约束|
|max_accel_mps2 / max_brake_mps2|2 / 3|m/s²，实际约束−3≤a≤2|硬约束|
|max_steer_rad|0.5|rad，实际约束转角±0.5|硬约束|
|max_curvature_per_m|0.2|1/m，实际约束曲率±0.2|硬约束|
|safety.margin_m|1.0|m，障碍物条件间距|硬约束|

这些车辆尺寸和1.2m偏移是原实验假设，不是实测nuScenes车身尺寸。T2对道路计入完整车身和道路不确定范围；对目标计入完整车身、目标总残差和1m安全间距，各一次。**1m障碍间距不再额外从道路扣除。**

## 3. 采集导出与模型接入

来源：[采集导出](/data2/hzy/MAP/agentic-driving/implementation/agentic/datasets/nuscenes_capture.py:59)、[实际clip接入配置](/data2/hzy/MAP/agentic-driving/implementation/results/long_clips_20261029_v2/following_deceleration/transport_config.json)。

|参数|实际值或已核对源码规则|作用/类别|
|---|---|---|
|图像数量|10|前向5帧＋其余5相机各1帧；真实采集输入|
|前向历史目标偏移|0、0.5、1、1.5、2s|从实际历史记录选最近帧，不生成新图像|
|其他相机|参考时刻之前、历史2s内的可用帧|异步输入不能假装同步|
|本例其他相机年龄|约0.091922、0.084267、0.074846、0.064904、0.007605s|仅为实际年龄，不因“小”自动免除目标对齐要求|
|自车速度估计|约0.5s因果后向定位差分；历史位置至少早0.1s|不是CAN瞬时车速；横向分量仅诊断|
|navigation_intent|沿可见可行驶道路继续前进，遵守可见信号并按需让行；不提供地图或未来ego路线|人工配置任务意图|
|实际服务|`https://api.openai.com/v1`，Responses|接入配置|
|请求/服务返回模型标识|`gpt-6.1-sol`|独立服务端快照仍未暴露|
|timeout_s|450|秒，保存的clip级配置；单次可用剩余时间还受运行截止约束|
|reasoning_effort|medium|生成配置|
|stream / store / tools|false / false / []|非流式、不存储、模型无额外工具权限|
|生成POST重试|0|失败照常消耗预算，无自动传输重试|
|GET就绪检查|最多3次|非生成连接检查，与生成次数分开|
|keepalive_expiry_s|120|秒，连接复用配置|
|单窗max_requests|2|本窗初答1次、语义修订最多1次|
|clip共享max_calls|38|19个参考时刻的共享上限，**不是本单窗预算**|
|wire / generator|`so_const_string_enum_type_v2` / `2.0.0`|只添加等价类型注记；原业务Schema仍独立校验|
|wire SHA|`c2e95df4406b292918a6bb85baa4a31b88467abeac613a7daa9e191eb021efbb`|实际传输Schema身份|
|business Schema SHA|`ae599d4744b9fe37ced1fd96cd883646daf6d0057dbc749cb805ed8a0819a6bd`|原生业务校验身份|

## 4. 模型决策与条件场景参数

来源：[本例record.json](/data2/hzy/MAP/agentic-driving/implementation/results/long_clips_20261029_v2/following_deceleration/runtime/frame_004/attempt_02/record.json)。

|字段|本例值|如何应用/类别|
|---|---|---|
|behavior|`hold_lane`|语义标签，本身不是独立车道中心线约束|
|target_region|[[-0.1,-1.5],[18,-1.5],[18,0.15],[-0.1,0.15]]m|整个时域车辆CENTER允许区域，空间硬约束|
|desired_speed_mps|6|m/s，速度跟踪软目标，不是立即到达6m/s或突破物理限制的许可|
|yield_to / stop|[] / null|无额外停车模板；其他目标的碰撞检查仍全部保留|
|priority|1|保留的语义元数据；本例不作为T3优先队列或独立优化项|
|hard_requirements|target_region、vehicle_limits、collision_free、road|T1验证必需项，后续工具落实数值硬约束|
|speed_weight|1|速度误差代价权重，软目标|
|acceleration_weight|0.5|加速度平方代价权重，软目标|
|steering_weight|1|转角平方代价权重，软目标|
|smoothness_weight|1|相邻控制差平方代价权重，软目标|
|road.polygon_m|[[-4,-3.8],[22,-6],[22,7],[-4,7]]m|模型局部道路假设，规划/核验的条件边界|
|road.uncertainty_m|0.45m|条件道路残差，计入道路边界检查|

权重是数值相对权重，不是概率；本实现没有把不同量纲的各代价解释为经校准的统一风险。

|目标ID|总残差(m)|对齐残差(m)|每源几何残差(m)|原生占用源数|
|---|---:|---:|---:|---:|
|oncoming_black_sedan|0.65|0.3|0.35|10|
|left_corner_man|0.9|0.4|0.5|1|
|left_edge_woman|1.2|0.5|0.7|1|
|right_gray_shirt_shorts|1.3|0.6|0.7|1|
|right_middle_dark_pedestrian|1.3|0.6|0.7|1|
|right_front_dark_man|1.3|0.6|0.7|1|
|right_edge_cropped_pedestrian|1.5|0.7|0.8|1|

这些有限残差是可证伪的模型条件假设，不是经校准的真实误差保证。总残差必须包含对齐残差与每个源几何残差之和。源多边形已经包含目标BODY和模式变化；工具不从速度另造未来预测。

## 5. T1：米制场景构造的规则与数值容差

来源：[metric_contract.py](/data2/hzy/MAP/agentic-driving/implementation/agentic/metric_contract.py:37)、[公共几何](/data2/hzy/MAP/agentic-driving/implementation/agentic/geometry.py:8)。以下是已核对当时源码默认/常量，不是模型估计值。

|参数或判定|数值/规则|作用/类别|
|---|---|---|
|原点、yaw、参考历史零值、中心偏移一致性|1e-8，对应m或rad|坐标/参考点数值容差|
|图像历史年龄|严格在[0,history_s]；用Decimal作时间相减|采集时间硬范围，无小时间差对齐豁免|
|前缀上限|prefix≤min(0.5s,step_s)|接口硬限制；本例为0.2s|
|旋转正交检查|`np.allclose(R.T@R,I,atol=1e-6)`，仍保留NumPy默认rtol|标定数值检查|
|旋转/内参行列式|abs(detR−1)<1e-6；abs(detK)>1e-9|标定有效性|
|多边形|有效且面积>1e-8m²；需要凸形时凸包面积差<1e-7m²|几何硬要求与数值容差|
|尺度锚点|与目标原生BODY维度按Decimal严格相等|条件先验内部一致性|
|异步状态位移界|`min(displacement_bound_m,max_speed_mps*age)+alignment.residual_m`，比较余量1e-8m|必须由模型提供的运动界，工具只做确定性核算|
|总残差预算|按Decimal检查alignment residual＋source residual≤total residual|硬一致性要求|
|源区间覆盖|每个保留情景严格无正时间缺口，覆盖完整2s|硬覆盖要求|
|初始BODY覆盖|t=0名义源必须包含全部车身角点，无以残差抵消缺角的豁免|硬几何要求|
|source到cell选源|闭边界相交，边界判断±1e-8s，保留原多边形|保守细化；不使正时间缺口检查失效|

T1保留原模型输出、源区间、条件前提和细分映射trace；它不补环境几何、不把null变为空占用。

## 6. T2：候选、车身走廊与连通性

来源：[候选定义](/data2/hzy/MAP/agentic-driving/implementation/agentic/t2.py:9)、[走廊构造](/data2/hzy/MAP/agentic-driving/implementation/agentic/t2.py:144)。

|参数|源码规则/本例值|作用/类别|
|---|---|---|
|candidate_count|5|有限候选发现，不是无限搜索|
|候选ID|original、brake_straight、brake_right、brake_left、brake_right_strong|同一模型场景和驾驶决策下的自车参考|
|非original种子转角|0、−0.18、+0.18、−0.22rad|再受真实转角与曲率限制截断；不是最终控制|
|制动种子|a=max(−max_brake,−v/dt)|遵守真实初速和制动上限，不一步变为负速|
|original种子|零转角，受加减速度限制趋近期望速度；有stop时改为停车模板|只用于凸域发现和T3初值|
|全局航向界|±0.35rad|T2/T3/T4一致的硬限制|
|每格候选航向带|端点seed yaw最小/最大值外扩0.025rad，再截在±0.35rad|配对的搜索硬范围|
|本例选中航向带|10个区间均[-0.025,+0.025]rad|实际保存值|
|参考点|后轴；完整BODY相对后轴x∈[-1.1,3.5]m，y∈[-0.9,0.9]m|车身支持函数几何|
|凸域面积|>1e-8m²|非空走廊要求|
|初始后轴包含|1e-7m buffer数值检查|必须包含真实初始后轴|
|相邻时间单元|交集非空|空间/时间连接要求|

search模式中的种子不是可执行解，本身可以不满足最终所有物理约束；真实初态、走廊连接、T3全部硬约束及T4仍必须成立。

本例5候选实际状态：original和brake_straight均T2/T3/T4通过；brake_right、brake_left和brake_right_strong在T2拒绝。选择通过候选中目标值最低的original。不能将候选耗尽解释为所有物理轨迹全局无解。

## 7. T3：约束优化

来源：[t3.py](/data2/hzy/MAP/agentic-driving/implementation/agentic/t3.py:11)。

|参数|值|作用/类别|
|---|---|---|
|状态变量X|4×11|后轴x、后轴y、yaw、v|
|控制变量U|2×10|a、前轮转角|
|曲率上界变量K|1×10；0≤K≤0.2m⁻¹|v2中约束K≥±curvature，构造光滑弧弦误差上界|
|状态转移|精确常加速度/常后轴曲率积分|动力学硬等式|
|半平面约束|端点投影＋`K*travel²/8`≤边界|连续弧段的保守几何约束|
|初始化|对应候选的状态与控制，K初值为abs(tan(seed steer)/wheelbase)|warm start，不改变可行域|
|max_iter|700|当时源码默认迭代上限|
|IPOPT tol / acceptable_tol|1e-8 / 1e-7|求解器数值容差|
|print_time / print_level / sb|false / 0 / yes|求解器输出控制|
|sinc近零分支|abs(z)<1e-4时四次展开|数值实现，不是物理安全间距|
|实际求解状态|Solve_Succeeded|保存的实测结果|
|实际迭代数|19|保存的实测结果|
|实际objective|4.949915881568494|所选候选代价，非安全分数|
|CasADi版本|3.7.2|**这是CasADi版本，不是IPOPT版本**；产物未单独登记IPOPT版本|

代价为：

```text
Σ dt × [speed_weight × (v−v_des)²
        + acceleration_weight × a²
        + steering_weight × steer²]
+ Σ smoothness_weight × ||U_k−U_(k−1)||²
```

目标区域、道路、碰撞、初态、速度/加速度/转角/曲率限制及适用的停车截止均为硬约束。速度接近期望值和平顺性是软目标。求解失败返回OPTIMIZATION_FAILED，不静默放松硬约束。

## 8. T4：连续完整车身核验

来源：[t4.py](/data2/hzy/MAP/agentic-driving/implementation/agentic/t4.py:17)。

|参数/检查|值|作用/类别|
|---|---|---|
|轨迹时间点一致性|1e-8s|检查11点与原网格一致|
|初态/后轴中心转换/动力学残差|1e-5，按分量使用m、rad或m/s|运动关系数值容差|
|曲率-转角关系|1e-6m⁻¹|检查curvature=tan(steer)/wheelbase|
|航向带、运动边界、道路/目标区域和碰撞比较|通常1e-6，对应各字段单位|数值比较余量|
|连续运动子段|最多0.02s；原0.2s段按ceil(dt/.02)细分|继续检查整子段，不是只查离散点|
|停车截止|落在子段中时显式切分|检查截止时刻，不只检查原网格点|
|BODY连续包络|两端完整有向BODY凸包＋`(1+r*abs(curvature))*L/2+1e-8m`|包含中间车身扫掠的保守余量|
|r|后轴到最远角点半径，约3.614m|仅用于该运动余量，不是以圆盘替代本分支BODY|
|目标占用|覆盖子段的原BODY源、全部保留情景和总残差|不插值缩小其他目标的占用|
|条件碰撞间距|clearance≥1m−1e-6m|1m间距硬要求的数值比较|
|停车截止后速度|≤1e-5m/s|仅stop适用时检查；本例stop=null|
|实际结果|PASS，0违例；检查到2s；释放0.2s|保存的实测结果|
|实际条件间距下界|1.0436279915714826m|以模型场景和覆盖条件成立为前提|

## 9. 模拟消费与独立接受器

来源：[SimulatedExecutor](/data2/hzy/MAP/agentic-driving/implementation/agentic/runtime.py:30)、[接受器](/data2/hzy/MAP/agentic-driving/implementation/agentic/acceptance.py:178)。

|参数/检查|值或规则|作用/类别|
|---|---|---|
|now与采集参考时刻|误差<1e-7s|执行时钟一致性|
|观察/场景/轨迹/核验身份|digest一致；核验token只能消费一次|执行授权硬条件|
|积分输入|已核验轨迹首段a、steer和释放时长|无隐藏世界，不控制真实车辆|
|接受器_close|绝对容差1e-8，各字段原单位|独立积分与回执比较|
|接受器重建|重建T1/T2、独立重算T4、独立积分并核对5类digest|不能只信任已有PASS文件|
|MOVING_PREFIX判据|max(前缀弧长,中心位移)>1e-5m|否则STATIONARY_HOLD；后者不等于移动任务完成|

所选解首段控制：a=−1.9340513337386995m/s²，steer=−0.02208512750691089rad，curvature=−0.008181007000843482m⁻¹。模拟消费0.2s后：

|量|实际保存值|
|---|---:|
|中心前向位移|1.6308428144989326m|
|中心横向位移|−0.02689258269852241m|
|中心位移模|1.6310645286459382m|
|后轴行驶弧长|1.6309980346388597m|
|航向变化|−0.013343206339742472rad|
|末速度|7.961585039820428m/s|
|接受器状态|ONLINE_SINGLE_WINDOW_SUCCESS|
|运动分类|MOVING_PREFIX|

接受器中旧内部字段 `strict_live_physical_success=true` 在本例表示局部条件式单窗口工具链和模拟前缀通过，不表示旧严格五秒实验通过。`multi_round_closed_loop_success=false`，`ground_truth_correctness=NOT_EVALUATED`。保存结果没有声明连续反事实闭环、G2通过或真实车辆安全。
