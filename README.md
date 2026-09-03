# Vast H3 Deployment Scripts

这是 H3 Worker 在 Vast.ai `vastai/comfy` 模板上的公开部署资源仓库。

桌面端在 Vast 实例进入 ready 后，按以下顺序手动部署：

1. `scripts/vast_comfy_bootstrap.sh` 等待 ComfyUI 基础环境就绪；
2. 执行 `scripts/setupp_h3_comfui_fast.sh` 安装 H3 节点、工作流和模型；
3. 从本仓库安装 `runtime/`、`config/` 和 `requirements-runtime.txt`，启动 Worker Gateway。
4. 只有 Gateway、ComfyUI 和 Worker `/ready` 全部通过后，才写入 ready 状态。

直接在 Vast 机器上运行稳定脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui.sh | bash
```

运行默认加速脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui_fast.sh | bash
```

桌面端默认使用：

```text
https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/vast_comfy_bootstrap.sh
```

## 手动 SSH 部署

实例创建请求不需要 `onstart`。实例状态变为 ready 后，桌面端通过 SSH 上传并启动脚本；Worker Token 只写入远程权限为 `600` 的环境文件，不放入命令行、日志或状态文件。

```bash
scp -P <ssh-port> scripts/vast_comfy_bootstrap.sh root@<ssh-host>:/run/h3/vast_comfy_bootstrap.sh
ssh -p <ssh-port> root@<ssh-host> 'chmod 700 /run/h3/vast_comfy_bootstrap.sh'
ssh -p <ssh-port> root@<ssh-host> 'umask 077; cat > /run/h3/worker.env; chmod 600 /run/h3/worker.env'
ssh -p <ssh-port> root@<ssh-host> 'nohup bash -lc "set -a; . /run/h3/worker.env; set +a; exec bash /run/h3/vast_comfy_bootstrap.sh" >>/var/log/h3/manual-bootstrap.log 2>&1 </dev/null >/dev/null 2>&1 &'
```

客户端通过标准输入写入环境文件内容，随后轮询 `/run/h3/bootstrap.json` 并增量读取 `/var/log/h3/bootstrap.log`。确认 Worker `/ready` 后，再通过 SSH 本地端口转发访问 Gateway `8190` 和 ComfyUI `18188`；这两个远程端口不需要公开暴露。

本仓库只保存公开部署代码和配置，不保存 Vast API key、Worker token 或模型文件。模型下载地址由脚本和配置中的公开 URL 控制。

## 目录

- `scripts/`：Vast 启动入口、稳定安装脚本、加速安装脚本和 backend 构建辅助脚本
- `runtime/`：GPU 检测、兼容性选择、缓存、健康检查和 Worker Gateway
- `config/`：GPU 兼容矩阵、模型清单示例和工作流目录
- `requirements-runtime.txt`：Worker runtime 依赖
