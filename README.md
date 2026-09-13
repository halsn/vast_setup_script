# Vast H3 Deployment Scripts

这是 H3 Worker 在 Vast.ai `vastai/comfy` 模板上的公开部署资源仓库。

当前仓库采用“基础 H3 + 独立部署 profile”的结构。所有 H3 部署脚本直接平铺在 `scripts/` 下，不使用 `experimental/` 子目录，也不再保留旧的混合 `fast` 部署入口。

## 部署流程

桌面端在 Vast 实例进入 ready 后，按以下顺序部署：

1. `scripts/vast_comfy_bootstrap.sh` 启动并等待 ComfyUI 基础环境就绪；
2. 根据所选 profile 执行对应 H3 部署脚本；
3. 从本仓库安装 `runtime/`、`config/` 和 `requirements-runtime.txt`，启动 Worker Gateway；
4. 只有 Gateway、ComfyUI 和 Worker `/ready` 全部通过后，才写入 ready 状态。

基础脚本负责官方 H3 节点/模型、SageAttention、工作流和健康检查；各 profile 只追加自身需要的节点、LoRA、checkpoint 或 runtime，避免复制整套基础部署逻辑。

## H3 部署 profiles

| Profile | 脚本 | 用途 |
| --- | --- | --- |
| Native | `scripts/setupp_h3_comfui.sh` | 原生 H3 + SageAttention，质量基线 |
| Turbo | `scripts/setupp_h3_comfui_turbo.sh` | LightX2V 4/8-step LoRA，原生 ComfyUI H3 节点 |
| PDD | `scripts/setupp_h3_comfui_pdd.sh` | Alibaba PDD 8-step，支持 FL2VA / Ref2VA |
| VDN | `scripts/setupp_h3_comfui_vdn.sh` | VDN-H3 hybrid attention，默认 8-step DMD stage |
| Cache | `scripts/setupp_h3_comfui_cache.sh` | Spectrum / FirstBlockCache |
| FastH3 | `scripts/setupp_h3_comfui_fasth3.sh` | FastVideo VSA + 4-step LoRA，目前以 FL2VA 为主 |

机器可读的 profile 元数据位于 `config/deployment_profiles.json`。`vast_workspace` 仍以应用 profile 为配置源，并实时发现 `scripts/` 下可部署脚本；这里的 JSON 用于脚本侧元数据、文档和校验，不重复实现一套工作台配置逻辑。

### Native

```bash
curl -fsSL https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui.sh | bash
```

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

## 公共 profile helper

`scripts/h3_profile_common.sh` 负责：

- 加载并执行 `setupp_h3_comfui.sh`；
- 复用基础脚本的 ComfyUI 路径、Python、节点安装、重启和健康检查逻辑；
- 下载 Hugging Face 单文件或 stage snapshot；
- 避免每个 profile 再复制一份大型 bootstrap。

远程运行默认拉取 `main` 上的基础脚本；本地/打包测试可以设置：

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

- `scripts/`：Vast 启动入口、H3 基础脚本、平铺的 H3 profile 脚本和 backend 构建辅助脚本
- `runtime/`：GPU 检测、兼容性选择、缓存、健康检查和 Worker Gateway
- `config/`：GPU 兼容矩阵、模型清单、工作台模板和 deployment profile 元数据
- `tests/`：bootstrap、gateway、workflow 和 profile 静态契约测试
- `requirements-runtime.txt`：Worker runtime 依赖
