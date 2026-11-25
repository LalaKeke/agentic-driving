# 主分支发布约定

日常算法 PR 向 `main` 交付可运行的脚本和测试。实验数据和测评产物保存在本地 `outputs/<实验名>/`。实验目录记录代码提交号、运行命令、配置、数据版本及实际测评结果。

1. 在研究分支开展实验。将可复用的代码改动做成独立提交。向 `main` 发布时，从最新 `origin/main` 建短分支，只带入选定的代码提交。检查 PR 的 **Files changed**。
2. 评分表、论文实验报告、日志、请求与响应、数据集图片、权重及视频留在实验归档。文件名和存放位置均应体现真实用途，包含实测结果的文档也遵守此约定。既有历史产物可以通过 PR 删除。
3. 依赖、运行配置、使用说明或仓库治理确需随代码更新时，在 PR 描述中单列涉及的文件和理由。当前允许的维护文件和目录见 [.github/scripts/release_scope.py](.github/scripts/release_scope.py)。新增发布路径时，先单独评审规则变更。
4. 合并前检查 `release-scope` 和 `frozen-replay`。前者按合并基点检查 PR 引入或修改的路径与文件类型，后者复核冻结选择和指标。审阅者核对允许路径内的实际内容。

在发布分支提交后，可于仓库根目录执行以下命令核对范围：

```bash
git fetch origin main
git diff --stat origin/main...HEAD
python3 -B .github/scripts/release_scope.py origin/main HEAD
```
