# 从干净检出准备 CorDriver 与 nuScenes

离线结果汇总可用系统 Python。从相机重新运行需要两套解释器。视觉与轨迹优化使用 Python 3.8、CUDA、旧 OpenMMLab 和 Shapely 1.8。独立连续几何检查使用 Python 3.11 和 Shapely 2 的向量化接口。上游版本、权重及外部预测的 SHA-256 见 [assets.json](assets.json)。

## 1. 环境

在仓库根目录运行。Linux 视觉机需要 NVIDIA 驱动、CUDA toolkit、Git、curl、patch、支持该 toolkit 的 C++ 编译器，以及带 pip/venv 的 Python 3.8 和 3.11。实测 A800 配置为 Python 3.8.20、PyTorch 1.9.1+cu111、MMCV 1.4.0、MMDetection 2.14.0、MMDetection3D 0.17.1、MMSegmentation 0.14.1，编译使用 CUDA toolkit 11.8、GCC 9 和 sm_80。

```bash
export CORDRIVER_TOOLS="$PWD/runs/tools"
export PYTHON38="$(command -v python3.8)"
export CUDA_HOME="$(dirname "$(dirname "$(command -v nvcc)")")"
export TORCH_CUDA_ARCH_LIST=8.0       # A800；其他 GPU 按其计算能力设置
export CC=gcc-9 CXX=g++-9             # 按本机兼容编译器设置
python3.11 -m venv .venv
.venv/bin/python -m pip install -r implementation/environment/requirements-control.txt
export CORDRIVER_CHECK_PYTHON="$PWD/.venv/bin/python"
bash implementation/environment/setup_vision.sh
```

脚本在 `CORDRIVER_TOOLS` 中准备指定提交的 CorDriver 和单独的视觉 venv，按上游 v0.17.1 源码包摘要下载 MMDetection3D 并编译 CUDA 扩展。随附的补丁适配当前 Numba 警告类的导入路径。视觉环境的软件版本与项目实际运行的主要库一致。新机器应运行脚本末尾的 GPU NMS、点盒算子以及 VAD 配置构建检查；依赖安装和 GPU 架构兼容性可以与现有工作站不同。已有视觉环境可直接复核：

```bash
project="$PWD"
(cd "$CORDRIVER_TOOLS/cordriver" && PYTHONPATH="$PWD" \
 "$CORDRIVER_TOOLS/cordriver_vision_env/bin/python" -B \
 "$project/implementation/environment/verify_stack.py")
```

## 2. 外部数据与权重

- 从 nuScenes 下载 v1.0-trainval 的图像、标定和元数据、CAN bus 及地图扩展，置于同一个 `NUSCENES_ROOT`。其中应有 `samples/CAM_*`、`v1.0-trainval`、`maps/expansion` 和 `can_bus`。官方整序列发布分支还读取其配置指定的传感器文件及验证注释。训练/验证集需依数据集条款取得。[nuScenes 数据及扩展](https://www.nuscenes.org/nuscenes#download)
- 将发布的 `corridor_epoch_48_learned_weight_12.pth` 与配套的 `corridor_nuscenes_infos_temporal_val.pkl` 放到 `$CORDRIVER_TOOLS/cordriver_assets/`。本实验使用 stage-2 的 12 步 learned-weight checkpoint，共 261,265,036 字节；val infos 共 6,019 行。请使用指定的 CorDriver info 文件。下载链接及独立计算的哈希见 [assets.json](assets.json) 和[作者仓库](https://github.com/Fudan-MAGIC-Lab/Drive-in-Corridors/tree/5a0ef06cb84156f6fa8510f5bf584403bf91c3dc)。
- 第二路地图预测取自同图像的 `sparsedrive_small_stage2`。使用上游独立 SparseDrive 环境及 stage-2 权重，按数据准备步骤生成 `data/infos/nuscenes_infos_val.pkl`，按时间顺序在 val 上推理，保存 `work_dirs/sparsedrive_small_stage2/{results.pkl,results_nusc.json,submission_vector.json}`。前者为检测和地图预测列表，后两个文件用于核对 sample token、检测与地图输出。参考导出文件有固定摘要，重新推理的浮点结果或 pickle 字节可能不同，比较新实验时须保存新来源；字节级复核使用清单中的导出版本。重建该前端的本地代码快照未保留 Git 提交信息，另列所用配置与权重文件摘要。[SparseDrive 运行说明](https://github.com/swc-17/SparseDrive/blob/main/docs/quick_start.md) · [stage-2 权重](https://github.com/swc-17/SparseDrive/releases/download/v1.0/sparsedrive_stage2.pth)
- 运行下方只读校验。CorDriver 的指定 Git 版本、关键源码、权重、索引和可选 SparseDrive 参考导出按 SHA-256 检查。nuScenes 检查数据布局与样例文件，逐 token 图像绑定和有效时间窗在运行入口检查。检验器不会加载 pickle 或发送网络请求。

```bash
: "${NUSCENES_ROOT:?指定许可数据集根目录}"
: "${SPARSEDRIVE_ROOT:?指定带验证预测导出的 SparseDrive 根目录}"
python3 implementation/environment/check_assets.py \
  --tools "$CORDRIVER_TOOLS" --data "$NUSCENES_ROOT" --sparse "$SPARSEDRIVE_ROOT"
```

基准发布代码使用相对路径 `data/nuscenes` 时，在本地运行目录将该位置链接到 `NUSCENES_ROOT`。重放现有 Astra 决策可离线进行。重新生成慢系统回答另需官方服务有权使用指定模型的凭据、连接和预算；请求的随机性可产生不同回答。
