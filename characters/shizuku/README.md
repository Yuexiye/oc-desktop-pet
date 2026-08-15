# Shizuku — 默认 Live2D 模型角色包

**Shizuku（しずく）** 是 Live2D 官方 Cubism SDK 的示例模型。许可允许**免费使用**（含个人/非商用场景），是开箱即用的默认 Live2D 桌宠的不二之选。

## 下载模型（本体不随仓库分发）

1. 打开 Live2D 官网下载页：<https://www.live2d.com/sdk/download/native/>
2. 下载 **Cubism SDK for Native**（Windows 版即可，模型模块跨平台通用）
3. 解压后，模型在以下路径：
   ```
   <SDK>/Samples/Resources/Shizuku/
   ```
   目录里包含：
   - `Shizuku.model3.json` ← 渲染器从它加载
   - `Shizuku.moc3`
   - `textures/`、`motions/`、`expressions/`（若有）
4. 把 **Shizuku 目录里的内容**（model3.json 这一层）复制到本目录：
   ```
   characters/shizuku/live2d/Shizuku.model3.json
   characters/shizuku/live2d/Shizuku.moc3
   characters/shizuku/live2d/textures/...
   characters/shizuku/live2d/motions/...
   ```

## 验证

启动桌宠后，把 config.json 的 `character` 改成 `shizuku` 即可作为默认桌宠：
```json
"character": "shizuku"
```
日志出现 `模型加载成功` 且角色正常显示即 OK。

## 许可说明

- Shizuku 属于 Live2D 官方示例模型，官方许可允许免费使用（详情以官网许可条款为准）。
- 商用/二次分发请遵循 Live2D 官方许可（一般需联系官方确认）。
- 本项目的 `characters/*/live2d/` 目录在 `.gitignore` 中排除——模型文件**不会**混入你的 git 仓库。