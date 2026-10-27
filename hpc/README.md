# 同济 HPC 部署与运行

本目录提供 CPU 原型的环境安装与 Slurm 测试脚本。当前工具链不需要 GPU，也不调用真实多模态模型。

## 当前目标

- SSH 别名：`tongji-u21012`，入口端口 `10022`。
- 项目目录：`/share/home/u21012/hzy/agentic-driving`。
- 独立环境：项目内 `.venv`，不修改账号原有 conda 环境。
- 默认账号/分区：`p_p21012` / `intel`；每次测试 1 CPU、1 GB、最长 5 分钟，无 GPU。

账号和分区来自本次服务器查询结果，并可通过 `HPC_ACCOUNT`、`HPC_PARTITION` 覆盖。遵守平台规则：**登录节点只准备代码和环境，测试及计算通过 Slurm 运行**。平台有资源计费，按实际调度和运行资源计算。

## 获取代码与安装环境

服务器能连接 GitHub 时：

```bash
cd /share/home/u21012/hzy
git clone https://github.com/LalaKeke/agentic-driving.git
cd agentic-driving
bash hpc/bootstrap_env.sh
```

若服务器不能连接 GitHub，可在可联网的本地工作区执行 `git bundle create agentic-driving.bundle main`，通过 SCP 传到目标父目录，再执行：

```bash
git clone -b main agentic-driving.bundle agentic-driving
cd agentic-driving
git remote set-url origin https://github.com/LalaKeke/agentic-driving.git
```

上述离线示例在 `/share/home/u21012/hzy` 中执行。凭据不写进仓库、脚本或 bundle。

将匹配 Linux x86_64 / CPython 3.11 的依赖 wheels 放入项目的 `wheelhouse/` 后，安装脚本会自动采用离线模式；否则使用 pip 索引。依赖固定在 `requirements-lock.txt`。

```bash
AGENT_BOOTSTRAP_PYTHON=/share/home/u21012/anaconda3/bin/python \
  bash hpc/bootstrap_env.sh
```

## 提交与查看

```bash
bash hpc/submit_smoke.sh
# 返回 Slurm 作业号
squeue -j <作业号>
sacct -j <作业号> --format=JobID,State,ExitCode,Elapsed,NodeList
cat runs/<作业号>/hpc_validation.json
cat runs/<作业号>/unit_tests.log
cat runs/<作业号>/pedestrian_demo.log
```

每次作业将指定提交的 `implementation/` 与 `hpc/` 快照展开到 `runs/<作业号>/`。所有变化的样例结果保存在该快照中，不覆盖 Git 管理的基线结果。`logs/`、`runs/`、`.venv/`、`wheelhouse/` 都被 Git 忽略。

提交脚本拒绝已跟踪文件的未提交改动，以保证日志中的提交号对应实际测试代码。`run_smoke.py` 默认拒绝在无 Slurm 作业环境时运行；开发者本地测试才可显式使用 `--local`。

## 更新代码

服务器可以访问 GitHub 时，在没有未提交修改的前提下执行：

```bash
git pull --ff-only
bash hpc/bootstrap_env.sh
bash hpc/submit_smoke.sh
```

若仍无法联网，传入一个新命名的 bundle，然后在已清洁的仓库执行 `git fetch /完整路径/新bundle main`、`git merge --ff-only FETCH_HEAD`。不要在服务器仓库中配置 Windows 的 `127.0.0.1:7890` 代理。

下一阶段接入真实基座模型时，API 密钥只使用本机权限受限的环境配置，不能提交 Git；另行确认计算节点的网络连通性。现有人工测试通过不等于 G2 验证通过。

本地代理经 SSH 转发的具体流程见 [NETWORK.md](NETWORK.md)，包含登录节点转发、计算节点跳转与不携带密钥的 Slurm 网络探针。
