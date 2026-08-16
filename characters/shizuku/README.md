# Shizuku — 默认 Live2D 模型角色包

**Shizuku（しずく）** 是 Live2D 官方 Cubism SDK 的示例模型。许可允许**免费使用**（含个人/非商用场景），是开箱即用的默认 Live2D 桌宠的不二之选。

## 前置条件（重要）

Live2D 渲染依赖 **live2d-sdk** Python 绑定包（`live2d-py`，Cubism Native 封装），
它**不在** requirements.txt 里（体积较大且需要编译/预编译 wheel）。首次使用前需安装：

```bash
pip install live2d-py
```

> 若安装失败（需要编译）：
> 1. 确认有 Visual Studio Build Tools（C++ 桌面开发工作负载）
> 2. 或尝试预编译 wheel：`pip install live2d-py --only-binary :all:`
> 3. 或到仓库 [live2d-py Releases](https://github.com/niwhsa9/live2d-py/releases) 下载对应 Python 版本的 wheel

启动时若缺少该包，日志会出现 `Live2DRenderer: 模型加载失败`，此时桌宠角色区域透明——
不是模型文件的问题，是 Python 绑定未安装。

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

## 换上官方 Shizuku 后，动作会有什么不同（重要）

**Shizuku 是官方完整模型，动作比当前 miku 丰富得多**。两者对比如下（2026-08 实测 miku 参数）：

| 部位 | 官方 Shizuku | 当前 miku（社区模型） |
|------|------------|---------------------|
| 手部/手臂 | ✅ 有（ParamArmLA/RA、ParamHandL/R）→ **能挥手** | ❌ 无手部骨骼，只能身体摆动 |
| 腿部/脚 | ✅ 有 → **能走/能坐** | ❌ 半身立绘，无腿部参数 |
| 身体 | ✅ BodyAngleX/Y/Z | ✅ 有（程序化层已驱动） |
| 表情 | ✅ 全套（眼/眉/嘴/鼓脸/吐舌） | ✅ 全套（程序化层已驱动） |
| 头发 | ✅ | ✅ |

**换上 Shizuku 你能立刻看到**：挥手、走动、身体语言——因为官方模型带了这些骨骼参数，而桌宠的程序化自主动作层（`_update_procedural_emotion`）会自动识别并驱动模型存在的参数，缺的参数自动跳过，不崩。

**切换建议**：如果你主要用 Live2D 且想要全身动作 → 用 Shizuku；如果喜欢 miku 形象 → 继续用 miku（表情/身体/头发已足够丰富，只是不能挥手/动腿）。

## 常见问题

**Q: 换了模型后，之前的动作配置还有效吗？**
A: 有效。动作触发逻辑（happy→眯眼笑、angry→皱眉等）在代码层，不看模型文件。模型有对应参数就生效，没有就跳过。

**Q: 想给模型加自定义动作？**
A: 直接编辑 `characters/<角色>/live2d/motions/*.motion3.json`——注意 Segments 数组必须是偶数长度、段标志用 0（Linear），否则 Live2D C++ 解析会崩溃（LOAD 阶段段错误）。

## 许可说明

- Shizuku 属于 Live2D 官方示例模型，官方许可允许免费使用（详情以官网许可条款为准）。
- 商用/二次分发请遵循 Live2D 官方许可（一般需联系官方确认）。
- 本项目的 `characters/*/live2d/` 目录在 `.gitignore` 中排除——模型文件**不会**混入你的 git 仓库。