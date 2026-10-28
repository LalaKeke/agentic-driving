# 实验可视化

打开 [index.html](index.html) 查看离线交互报告。图像与图表均内嵌，无需登录、模型服务或联网；可直接用浏览器打开，也可使用 VS Code 的 HTML 预览。相邻原始证据链接需要保留项目目录结构。

- 四个真实 nuScenes 观测窗口：40 张采集图像、10 份真实响应；按原协议与独立 2.1 格式复核标识。查看停车让行决策、未知字段、T1 拒绝原因和反馈记录。单独的评价采样标注为没有调用 T1。
- G2：两个事后选择目标的点位误差及俯视点位对比，明确速度与有效完整未来占用不可评分。GT 辅助审阅图需展开才显示，未提供给模型。
- 人工案例：对应 `runs/20261027T105505Z-8a812146`，可切换被拒候选和修订候选，回放道路、后轴参考点走廊、车身、保守圆和区间占用。另展示 26 次实际前缀模拟的速度。

**真实模型没有释放轨迹；人工案例通过不代表真实图像闭环通过。** 当前所选人工运行最低记录速度约 0.149m/s，未完全停稳；单次修订规划中的停车目标与重复重规划的累计执行结果分别显示。

导出：

- [五页图表 PDF](figures.pdf)
- [四例总览 PNG](real_cases.png) / [SVG](real_cases.svg)
- [字段可用性 PNG](availability.png) / [SVG](availability.svg)
- [G2 诊断 PNG](g2_diagnostics.png) / [SVG](g2_diagnostics.svg)
- [G2 点位对比 PNG](g2_positions.png) / [SVG](g2_positions.svg)
- [人工轨迹 PNG](synthetic_trajectory.png) / [SVG](synthetic_trajectory.svg)
- [输入/输出 SHA256、库版本](manifest.json)

复现（只读原结果；不调用模型）：

```bash
cd /data2/hzy/MAP/agentic-driving
# 当前系统 python 已有 matplotlib、Pillow；不修改其他项目环境。
python implementation/visualization/build_report.py
```

其他服务器可在独立报告环境安装 `implementation/visualization/requirements.txt`。重新构建需原数据集图像和上述人工运行日志；查看已交付报告不需要数据集。字体优先使用本机 Noto Sans CJK，字体缓存写入本项目 `runs/visualization`。

[完整真实试验报告](../gpt61_live/README.md) 的原始科学结论保持：会员图像调用跑通，米制场景契约未满足，G2 不判通过。

实际浏览器验收：[browser_checks.json](browser_checks.json)。Chromium 141 / Playwright 1.56.1 检查通过：40 帧解码、10 份输出一致、两种候选与播放、全部章节、390px 窄屏；JavaScript 错误及外部网络请求均为 0。来源一致性见 [offline_checks.json](offline_checks.json)。这不是新增驾驶或 G2 通过证据，92 项软件测试计数沿用原报告。
