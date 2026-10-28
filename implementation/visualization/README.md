# 离线实验报告生成器

从已经归档的 GPT-6.1 nuScenes 输出与指定人工运行生成报告，不调用模型、工具求解器或控制接口。仅在报告阶段读取 GT 对应关系和标注辅助图。所有原数据/结果只读；源文件 SHA256、报告解释器依赖版本记录在输出 manifest 中。

```bash
cd /data2/hzy/MAP/agentic-driving
python implementation/visualization/build_report.py
```

当前系统 Python 已安装锁定的 matplotlib/Pillow，驾驶项目 `.venv` 不需新增绘图依赖。新机器可用独立环境安装 `requirements.txt`；构建还需要原数据集图像和 `runs/20261027T105505Z-8a812146` 人工运行。默认产物为 `implementation/results/visual_report`。已交付 HTML 的图像和图表全部内嵌，直接查看不依赖 Python、数据集或网络。原始证据链接需保留相邻目录。

图表采用 matplotlib，提供 PNG/SVG 与五页 PDF。中文字体优先使用 Noto Sans CJK。浏览器端只使用原生 JavaScript/SVG，包含图像历史播放、响应/修订选择和人工规划时间回放。T2 走廊是后轴参考点配置空间；不将该区域误画成完整车身可任意放置的道路。

`verify_browser.cjs` 对生成页面进行实际浏览器检查，使用隔离在项目 `runs/tools/visualization` 下的 Playwright 1.56.1 与其 Chromium Headless Shell。检查 40 帧解码、10 份模型输出一致性、候选/时间切换、两类播放、所有选项卡、390px 屏宽溢出、JavaScript 错误与外部请求。它的 PASS 仅表示报告渲染/交互通过，不是新的驾驶或 G2 科学验证。

```bash
# 可选，仅首次安装浏览器验收工具；不改全局 Node/Python 环境
npm install --prefix runs/tools/visualization --save-exact playwright@1.56.1 \
  --ignore-scripts --no-audit --no-fund
PLAYWRIGHT_BROWSERS_PATH="$PWD/runs/tools/visualization/browsers" \
  node runs/tools/visualization/node_modules/playwright/cli.js install chromium --only-shell
PLAYWRIGHT_BROWSERS_PATH="$PWD/runs/tools/visualization/browsers" \
  node implementation/visualization/verify_browser.cjs
```

浏览器验证工具是可选开发依赖，不属于查看报告或构建静态图表所需依赖。
