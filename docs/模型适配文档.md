# 模型适配三步指南

> 三步把模型跑起来：放文件 → 调定位 → 配表情

---

## 第一步：放文件

### 1.1 准备模型

获取一个**有分发许可**的 Live2D 模型（`.moc3` + 纹理 + `.model3.json`）。

免费模型源：
- Live2D 官方 [CubismWebSamples](https://github.com/Live2D/CubismWebSamples)（Haru / Hiyori 等）
- 或运行 `python tools/fetch_free_live2d_sample.py haru` 一键拉取官方样例

### 1.2 复制到角色目录

把模型文件放入 `characters/<id>/live2d/`：

```
characters/<id>/
├── pet.json                      ← 角色元数据（必填）
├── identity.md                   ← 可选，角色身份描述
└── live2d/
    ├── <name>.moc3               ← 模型本体
    ├── <name>.model3.json        ← 模型清单
    ├── <name>.physics3.json      ← 物理效果（可选）
    ├── <name>.cdi3.json          ← 显示信息（可选）
    ├── textures/                 ← 纹理
    ├── motions/                  ← 动作
    └── expressions/              ← 表情（可选）
```

### 1.3 修正 model3.json 的引用路径

原模型的 `.model3.json` 里纹理/动作路径可能指向原目录结构，需要改成相对 `live2d/` 的路径。

**把 `model3.json` 丢给 AI**：让 AI 帮你检查路径是否相对正确。

```json
{
  "FileReferences": {
    "Moc": "<name>.moc3",
    "Textures": ["textures/texture_00.png"],
    "Motions": { "": [{"File": "motions/idle.motion3.json"}] },
    "Expressions": [{"Name": "比心", "File": "expressions/比心.exp3.json"}]
  }
}
```

### 1.4 写 pet.json

```json
{
  "id": "<id>",
  "name": "<角色名>",
  "description": "模型作者信息",
  "style": "live2d",
  "format": "live2d",
  "live2d": {
    "scale": 1.0,
    "offset": [0, 0]
  },
  "scale": 1.0
}
```

---

## 第二步：调定位

### 2.1 问题：模型显示位置不对

不同模型的默认位置不同。需要通过 `pet.json` 的 `live2d.scale` 和 `live2d.offset` 调整。

### 2.2 调整 scale（缩放）

```json
{
  "live2d": {
    "scale": 1.0,  // 默认 1.0，范围 0.5~2.0
    "offset": [0, 0]
  }
}
```

**常见问题**：
- 模型太大：`scale` 降到 0.8
- 模型太小：`scale` 升到 1.2
- 模型居中但显示不全：`scale` 降到 0.9

### 2.3 调整 offset（偏移）

```json
{
  "live2d": {
    "scale": 1.0,
    "offset": [x, y]  // [水平偏移, 垂直偏移]，单位像素
  }
}
```

**常见问题**：
- 模型偏左：`offset[0]` 加正数
- 模型偏右：`offset[0]` 加负数
- 模型偏上：`offset[1]` 加负数（Live2D Y 轴向上）
- 模型偏下：`offset[1]` 加正数

### 2.4 实测错觉：刘海挡眉

**现象**：模型刘海遮住眉毛，表情切换时眉毛动不了。

**原因**：刘海层的参数索引与眉毛冲突，或刘海层覆盖了眉毛层。

**解决**：
1. 检查 `model3.json` 的 `Groups` 段，确认眉毛参数未被刘海覆盖
2. 在 `live2d_renderer.py` 的 `_cache_watermark_index` 里跳过刘海层
3. 或修改 `emote_presets.py` 的眉毛参数值，降低幅度

**把 `model3.json` 和 `emote_presets.py` 丢给 AI**：让 AI 帮你检查参数冲突。

### 2.5 实测错觉：动作盖表情

**现象**：播放动作时，表情被动作覆盖，表情切换失效。

**原因**：动作文件（`.motion3.json`）里包含表情参数，优先级高于手动设置的表情。

**解决**：
1. 在 `model3.json` 的 `Groups` 段，把表情参数从动作组里移除
2. 或修改 `motion_mixer.py` 的 `Layer` 优先级，让表情层优先于动作层
3. 或清空动作文件里的表情参数（手动编辑 `.motion3.json`）

**把 `model3.json` 和动作文件丢给 AI**：让 AI 帮你检查参数优先级。

---

## 第三步：配表情

### 3.1 情绪 → 表情映射

桌宠按情绪关键词自动匹配表情。映射表在 `avatar/live2d_renderer.py` 的 `_EMOTION_KEYWORDS`：

| 情绪 | 文件名关键词（动作） | 文件名关键词（表情） |
|---|---|---|
| happy | happy / joy / smile / fun / 唱歌 / 比心 | 同左 |
| angry | angry / mad | (可加) |
| sad | sad / cry | (可加) |
| surprised | surprise / shock / 圈圈 / 前倾 | 同左 |
| thinking | think / doubt / 圈圈 / 前倾 | 同左 |
| cute | (可加 脸红) | 脸红 |
| neutral | idle | — |

### 3.2 配置表情

在 `model3.json` 的 `Expressions` 段声明表情：

```json
{
  "FileReferences": {
    "Expressions": [
      {"Name": "比心", "File": "expressions/比心.exp3.json"},
      {"Name": "happy", "File": "expressions/happy.exp3.json"},
      {"Name": "angry", "File": "expressions/angry.exp3.json"}
    ]
  }
}
```

**表情名是中文也能匹配**，只要文件名含情绪关键词。

### 3.3 自定义表情

想加自定义表情：

1. 在 `emote_presets.py` 的 `LIVE2D_PRESETS` 加预设步骤
2. 在 `SPRITE_PRESET_MAP` 加精灵图映射
3. 在 `_EMOTION_KEYWORDS` 加关键词

**把 `emote_presets.py` 丢给 AI**：让 AI 帮你加新表情。

### 3.4 实测错觉：表情不切换

**现象**：对话时表情不切换，一直是 neutral。

**原因**：
1. `model3.json` 没有 `Expressions` 段
2. 表情文件名不含情绪关键词
3. 表情文件路径错误

**解决**：
1. 检查 `model3.json` 的 `Expressions` 段
2. 确认表情文件名含情绪关键词（如 `happy.exp3.json`）
3. 检查表情文件路径是否相对 `live2d/`

**把 `model3.json` 和表情文件列表丢给 AI**：让 AI 帮你检查。

---

## 把哪个文件丢给 AI

| 步骤 | 文件 | 让 AI 做什么 |
|---|---|---|
| 放文件 | `model3.json` | 检查路径是否相对正确 |
| 调定位 | `pet.json` | 调整 scale 和 offset |
| 刘海挡眉 | `model3.json` + `emote_presets.py` | 检查参数冲突 |
| 动作盖表情 | `model3.json` + 动作文件 | 检查参数优先级 |
| 配表情 | `model3.json` + 表情文件列表 | 检查表情映射 |
| 自定义表情 | `emote_presets.py` | 加新表情预设 |

---

## 快速验证

```bash
# 检测角色格式是否识别
python -c "from avatar.factory import detect_format; print(detect_format('<id>'))"
# 预期输出: live2d

# 枚举已安装角色包
python -c "from core.character_package import CharacterPackageManager; print([p.agent_id for p in CharacterPackageManager().list_installed_packages()])"
```

---

## 常见问题

- **模型加载失败/白屏**：看日志 `logs/oc_pet.log` 里 `Live2DRenderer: 模型加载失败: ...`。常见原因：model3.json 引用路径不对、moc3 版本与 live2d-py 不兼容。
- **表情不切换**：确认 `model3.json` 有 `Expressions` 段，且表情文件名含对应情绪关键词。
- **动作不循环**：`motions/` 下要有 `idle.motion3.json`（待机动作）。
- **改完不生效**：重启桌宠。

---

## 参考

- `docs/如何添加桌宠角色.md` — 完整教程
- `characters/miku/` — Live2D 参考
- `characters/yuexinmiao/` — 精灵图参考
