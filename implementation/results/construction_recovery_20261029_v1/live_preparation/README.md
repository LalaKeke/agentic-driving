# 新独立串行小试准备（未启动）

**本准备新增真实调用0，`launched=false`。** [计划](plan.json)限定施工最多2次、红灯最多2次，总计4次，失败计预算，无传输重试；不继承旧批次剩余2次。模型服务、认证、Schema或额度错误立即停止外呼。

- [施工输入](construction_capture.json)：原10张图像及原采集/车辆/安全值保留，只给实际引用的CAM_FRONT_LEFT增加两张因果历史图，距参考1.907616s和1.007616s。CAM_FRONT已有5张，不重复增加。共12图。
- [红灯标签输入](redlight_capture.json)：原capture逐字节复制，共10图。标签不作为交通事实输入；信号状态、归属由模型从图像判断，正确静止等待是有效结果。
- [施工初答反馈](construction_initial_feedback.json)：来自保存原生输出的左侧行人/右侧工人ID、证据帧与原占用源区间；指出T2空走廊并明确它不是全局不可行证明。要求根据新历史证据审视身体点、朝向、运动条件和残差，保留不利分支，不要求移动障碍或扩大路面。

[输入说明和SHA](input_summary.json)保存原10图与新增图指纹。准备只通过既有`Acquisition`读取`sample/sample_data/ego_pose/sensor/calibrated_sensor`五表，不读取未来帧、地图或注释。生成脚本见父目录`prepare_live_inputs.py`。

本计划仍是准备材料：实际运行前须由主执行器冻结当前源码及选定规划器，另建实际运行配置。现有`run_local_prototype.py`还没有初答反馈CLI参数，本准备不修改入口，也不绕过其预算。施工须先确认阻塞确属模型信息问题；规划器近似不能交给模型改场景解决。
