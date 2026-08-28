# Vast H3 Deployment Scripts

这是 H3 Worker 在 Vast.ai `vastai/comfy` 模板上的公开部署资源仓库。

实例启动时按以下顺序执行：

1. `scripts/vast_comfy_bootstrap.sh` 等待 ComfyUI 基础环境就绪；
2. 执行 `scripts/setupp_h3_comfui_fast.sh` 安装 H3 节点、工作流和模型；
3. 从本仓库安装 `runtime/`、`config/` 和 `requirements-runtime.txt`，启动 Worker Gateway。

直接在 Vast 机器上运行稳定脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui.sh | bash
```

运行默认加速脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui_fast.sh | bash
```

桌面端和 Vast `onstart` 默认使用：

```text
https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/vast_comfy_bootstrap.sh
```

本仓库只保存公开部署代码和配置，不保存 Vast API key、Worker token 或模型文件。模型下载地址由脚本和配置中的公开 URL 控制。

## 目录

- `scripts/`：Vast 启动入口、稳定安装脚本、加速安装脚本和 backend 构建辅助脚本
- `runtime/`：GPU 检测、兼容性选择、缓存、健康检查和 Worker Gateway
- `config/`：GPU 兼容矩阵、模型清单示例和工作流目录
- `requirements-runtime.txt`：Worker runtime 依赖
