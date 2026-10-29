# 同连接模型预检后的五时刻恢复组

已结束：5次生成POST均返回HTTP200和通过原业务Schema的原生JSON；frame001一次PREMISE_SCOPE修订后通过，frame002–004各初答通过，均完成T1–T4、独立接受器及0.2秒移动转弯前缀。frame005的单次非生成/models预检ConnectError，未发送POST，所有模型/工具/消费阶段NOT_REACHED，立即停止。

明确变化仅为显式系统SSLContext及同client单次认证GET/models，证书校验和零重试保持。预检4次HTTP200、最后1次连接失败；尚未解决所有代理/上游连接问题，不能称长期稳定。业务Schema、提示词、车辆、道路/目标占用处理、1米间距、规划与核验算法未变。

[配置](plan.json)、[实际账本](batch_summary.json)、[417项最终离线回归](offline_verification/unittest.txt)与[来源](offline_verification/provenance.json)保留。源码357e1e6、注册ed4f00d；测试60.374秒、0真实调用。定向10项接入及4项预算回归不累计成独立测试样本。

[frame000零调用复算](offline_replay_frame000/summary.json)重建并核验原真实输出，位移/末速与原在线结果完全相同；它不是新增在线成功。[最终合并展示](../api_continuous_bend_20261029_v2/README.md)保留全部三组失败与成功。
