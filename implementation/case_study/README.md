# 0018／31 的有向目标与第二路预测边界

这是开发样例的五次控制求解回放。输入文件保存两套视觉前端的数值预测、原 QP 输入、先前平滑求解的种子控制、当时的 Astra 指令、固定的走廊以及来源 SHA-256。给定同一有向目标，先用原种子定位第二路边界与车身的相交处，将边界的分离条件加入求解，再按原主道路、预测对象、车身、动力学和目标条件验收。这个案例的开发修复单列于 99 帧的冻结对照。

在根目录运行。需要 [CorDriver 源码、QP 环境及独立检查环境](../environment/README.md)。此例使用已保存的预测，无需相机、网络或 GPU。

```bash
out=$(mktemp -d)/repair
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLBACKEND=Agg \
  runs/tools/cordriver_vision_env/bin/python -B implementation/case_study/replay.py --out "$out"
```

输出目录包含全部尝试的轮廓、控制、重积分轨迹、道路及对象检查收据。登记环境中第五次控制求解获准，最终位置窗最大超量为 0.27643148 m，属于近似解。第二路检查为 UNKNOWN，在原协议下没有触发显式相交否决。冻结轨迹的开环独立评分为 ACR=0、CCR=0，三秒前缀 L2 为 0.353 m；重新求解后可对照 [案例与评分快照](../evaluation/README.md)。
