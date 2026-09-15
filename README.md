# Vast H3 Deployment Scripts

这是 H3 Worker 在 Vast.ai `vastai/comfy` 模板上的公开部署资源仓库。

当前仓库采用“内部 H3 base core + 共享能力层 + 用户可选 deployment profile”的结构。所有用户可选 H3 部署脚本仍平铺在 `scripts/` 下；内部基础实现使用 `scripts/h3_comfui_base.sh`，不会作为单独部署选项暴露。

## 部署流程

桌面端在 Vast 实例进入 ready 后，按以下顺序部署：

1. `scripts/vast_comfy_bootstrap.sh` 启动并等待 ComfyUI 基础环境就绪；
2. 根据所选 profile 执行对应 H3 部署脚本；
3. 用户可选 profile 统一经过 `scripts/h3_profile_common.sh`，先运行 H3 base core，再安装共享媒体预览与 Refine/Latent 能力；
4. 从本仓库安装 `runtime/`、`config/` 和 `requirements-runtime.txt`，启动 Worker Gateway；
5. 只有 Gateway、ComfyUI 和 Worker `/ready` 全部通过后，才写入 ready 状态。

内部 base core 负责官方 H3 节点/模型、SageAttention、工作流和基础健康检查；共享 profile helper 负责所有模式都需要的 VideoHelperSuite、3D Refine、`MpiSaveLatent` / `MpiLoadLatent`；Turbo/PDD/VDN/Cache/FastH3 只追加自己的加速节点、LoRA、checkpoint 或 runtime。

因此 **Native、Turbo、PDD、VDN、Cache、FastH3 默认都具备生成后高清增强能力**，不再单独提供 Refine deployment profile。

## H3 部署 profiles

| Profile | 脚本 | 用途 |
| --- | --- | --- |
| Native | `scripts/setupp_h3_comfui.sh` | 原生 H3 + SageAttention，质量基线 |
| Turbo | `scripts/setupp_h3_comfui_turbo.sh` | LightX2V 4/8-step LoRA，原生 ComfyUI H3 节点 |
| PDD | `scripts/setupp_h3_comfui_pdd.sh` | Alibaba PDD 8-step，支持 FL2VA / Ref2VA |
| VDN | `scripts/setupp_h3_comfui_vdn.sh` | VDN-H3 hybrid attention，默认 8-step DMD stage |
| Cache | `scripts/setupp_h3_comfui_cache.sh` | Spectrum / FirstBlockCache |
| FastH3 | `scripts/setupp_h3_comfui_fasth3.sh` | FastVideo VSA + 4-step LoRA，目前以 FL2VA 为主 |

机器可读的 profile 元数据位于 `config/deployment_profiles.json`。`vast_workspace` 仍以应用 profile 为配置源，并实时发现 `scripts/` 下以 `setupp_h3_comfui` 开头的用户可选部署脚本；内部 `h3_comfui_base.sh` 不会进入部署列表。

### Native

```bash
curl -fsSL https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui.sh | bash
```

Native 现在也是一个薄 profile：它与其它加速 profile 一样经过共享能力层，因此默认包含 Refine 与 packed AV Latent 保存/加载。

### Turbo

默认安装 LightX2V FL2VA 4-step 和 8-step LoRA：

```bash
curl -fsSL https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui_turbo.sh | bash
```

可选安装 Ref2VA：

```bash
H3_TURBO_INSTALL_REF2VA_4=1 H3_TURBO_INSTALL_REF2VA_8=1 \
  bash setupp_h3_comfui_turbo.sh
```

### PDD

默认安装 FL2VA 和 Ref2VA 官方 8-step PDD 权重：

```bash
curl -fsSL https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui_pdd.sh | bash
```

### VDN

默认安装 `stage-dmd-step-250`：

```bash
curl -fsSL https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui_vdn.sh | bash
```

消费卡可切换预量化 INT8 branch：

```bash
H3_VDN_USE_INT8_STAGE=1 bash setupp_h3_comfui_vdn.sh
```

VDN 的 8-step DMD adapter 不应再叠加社区 Turbo LoRA。

### Cache

Spectrum：

```bash
H3_CACHE_METHOD=spectrum bash setupp_h3_comfui_cache.sh
```

FirstBlockCache：

```bash
H3_CACHE_METHOD=firstblock bash setupp_h3_comfui_cache.sh
```

一次只启用一种 cache 路线。Cache profile 自己安装节点并生成 `H3_Cache_Active.json`，不再依赖旧 `fast` 脚本。

### FastH3 / VSA

```bash
curl -fsSL https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui_fasth3.sh | bash
```

该 profile 会安装 FastVideo VSA 节点、4-step LoRA 和 gate。当前以 FL2VA 为主；VSA 是 attention 加速，不负责降低 activation VRAM。

## 共享 Refine / 生成后高清增强

所有用户可选 H3 profile 默认安装固定版本的 3D latent 二采节点与 FP16 checkpoint，同时安装固定版本的 `ComfyUi-MpiNodes`。其中 `MpiSaveLatent` / `MpiLoadLatent` 可以正确保存和恢复 MiniMax H3 的 packed 视频+音频 Latent。

profile 完成安装并重启 ComfyUI 后，共享层会通过 `/object_info` 强校验以下节点已经注册：

- `MinimaxH3LatentUpscaler3DRefineHandoff`
- `MpiSaveLatent`
- `MpiLoadLatent`

工作台检测到这些能力后，新生成任务会保留一采 packed AV Latent。之后可直接从历史记录启动独立二采高清增强：从保存的 Latent 继续，不重新执行一采 sampler。旧任务如果当时没有保存 Latent，仍可正常查看和重新生成，但不能直接走这条生成后增强链路。

Refine 默认开启。只有在明确需要节省约 3 GB 额外磁盘空间、且接受关闭历史高清增强时，才手动设置：

```bash
H3_INSTALL_REFINE=0 bash setupp_h3_comfui.sh
```

同一环境变量也适用于 Turbo/PDD/VDN/Cache/FastH3。

## 公共 profile helper

`scripts/h3_profile_common.sh` 负责：

- 加载并执行内部 `scripts/h3_comfui_base.sh`；
- 安装固定版本 VideoHelperSuite，提供浏览器兼容的视频/音频预览；
- 安装并验证共享 3D Refine、Refine checkpoint 和 MPI packed latent 保存/加载节点；
- 复用 base core 的 ComfyUI 路径、Python、节点安装、重启和健康检查逻辑；
- 下载 Hugging Face 单文件或 stage snapshot；
- 避免每个 profile 再复制一份大型 bootstrap。

远程运行默认拉取 `main` 上的内部 base core；本地/打包测试可以设置：

```bash
H3_PROFILE_USE_LOCAL_BASE=1
```

## 手动 SSH 部署

实例状态变为 ready 后，桌面端通过 SSH 上传并手动启动脚本；脚本会按需启动官方 ComfyUI 基础 entrypoint，但不会在实例创建时部署 H3。Worker Token 只写入远程权限为 `600` 的环境文件，不放入命令行、日志或状态文件。

```bash
scp -P <ssh-port> scripts/vast_comfy_bootstrap.sh root@<ssh-host>:/run/h3/vast_comfy_bootstrap.sh
ssh -p <ssh-port> root@<ssh-host> 'chmod 700 /run/h3/vast_comfy_bootstrap.sh'
ssh -p <ssh-port> root@<ssh-host> 'umask 077; cat > /run/h3/worker.env; chmod 600 /run/h3/worker.env'
ssh -p <ssh-port> root@<ssh-host> 'nohup bash -lc "set -a; . /run/h3/worker.env; set +a; exec bash /run/h3/vast_comfy_bootstrap.sh" >>/var/log/h3/manual-bootstrap.log 2>&1 </dev/null >/dev/null 2>&1 &'
```

客户端通过标准输入写入环境文件内容，随后轮询 `/run/h3/bootstrap.json` 并增量读取 `/var/log/h3/bootstrap.log`。确认 Worker `/ready` 后，再通过 SSH 本地端口转发访问 Gateway `8190` 和 ComfyUI `18188`；这两个远程端口不需要公开暴露。

本仓库只保存公开部署代码和配置，不保存 Vast API key、Worker token 或模型文件。模型下载地址由脚本和配置中的公开 URL 控制。

## 目录

- `scripts/h3_comfui_base.sh`：内部 H3 base core，不作为用户 deployment profile 暴露
- `scripts/h3_profile_common.sh`：所有 H3 profile 的共享能力层
- `scripts/setupp_h3_comfui*.sh`：用户可选 H3 deployment profile
- `runtime/`：GPU 检测、兼容性选择、缓存、健康检查和 Worker Gateway
- `config/`：GPU 兼容矩阵、模型清单、工作台模板和 deployment profile 元数据
- `tests/`：bootstrap、gateway、workflow 和 profile 静态契约测试
- `requirements-runtime.txt`：Worker runtime 依赖
