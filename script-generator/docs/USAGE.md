# 剧本生成器 · 详细用法

> 项目总览见[根目录 README](../../README.md)；设计取舍见 [ARCHITECTURE.md](ARCHITECTURE.md)。
> 本文是命令、参数、配置的完整参考。

把 `material-library` 的 465 条素材变成一部 **30 集 × 60 秒**短剧。

---

## 30 秒跑起来

```bash
cd script-generator

# 1. 素材库 -> 机器可读要素包（只需跑一次；素材库更新后重跑）
python tools/build_elements.py --market MX

# 2. 看蓝图会编译成什么样（不调模型，秒回）
python cli.py plan -b examples/blueprint-bag1.json

# 3. 出稿（模板模式，不需要 API key）
python cli.py gen -b examples/blueprint-bag1.json -m template -r 1-6 -o out

# 4. 起界面
python server.py            # http://127.0.0.1:8765
```

零第三方依赖，Python 3.10+ 即可（开发验证用 3.13）。

---

## 接模型

**方式一：界面里填（推荐）**

点右上角 **⚙ 模型** → 选一个「一键填入」的厂商 → 补 API Key → **测试连接** → 保存。

配置存在 `config.local.json`（已在 `.gitignore` 里），重启后仍在。Key 只留本机，
页面只回显尾号 `***abcd`，不回传明文。

**方式二：环境变量**（优先级高于界面配置，适合 CI / 容器）

```bash
# DeepSeek
export SG_LLM_BASE_URL=https://api.deepseek.com/v1
export SG_LLM_API_KEY=sk-xxxx
export SG_LLM_MODEL=deepseek-chat

# OpenAI / 通义 / 智谱 / 本地 Ollama 同理，只换 base_url 与模型名
export SG_LLM_BASE_URL=http://127.0.0.1:11434/v1   # Ollama
export SG_LLM_API_KEY=ollama
export SG_LLM_MODEL=qwen2.5:14b
```

其余可调项：`SG_LLM_TIMEOUT`(120) `SG_LLM_MAX_RETRIES`(3) `SG_LLM_TEMPERATURE`(0.8)
`SG_LLM_JSON_MODE`(1) `SG_LLM_MAX_TOKENS`(4000)

没配 key 时自动走**模板降级**——仍然出 0 阻断的完整稿，只是文采待润色。

---

## 规格：30 集 × 60 秒

默认就是 **30 集、单集 60 秒**（竖屏短剧的付费卡点密度）。集数 1–200、单集 15–180 秒，
在界面顶部直接改，或写进蓝图 `meta`：

```jsonc
"meta": { "episode_count": 30, "episode_seconds": 60 }
```

改秒数会连带改三件事，都是自动的：
1. **五段时间轴**（60 秒：`0-3 / 3-12 / 12-40 / 40-54 / 54-60`；90 秒另一套）
2. **单集成本门槛**（60 秒：爽点 ≤6 只上 2 人、≤6 不换景）
3. **台词密度**（60 秒控制在中文 130–180 字 / 西语 90–120 词，一场戏 ≤8 句）

---

## 要素按什么顺序选

界面的顺序就是用户的思考顺序，不是分类顺序：

| 步 | 阶段 | 定什么 |
|---|---|---|
| 1 | **题材** | 组合包（8 选 1）。选完自动带出剧情内核与主线骨架的推荐位 |
| 2 | **剧情** | 剧情内核（讲什么）+ 主线骨架（怎么走） |
| 3 | **张力** | 冲突内核 ×3，必须三层齐 |
| 4 | **收束** | 结局走向（怎么收 + 什么代价） |
| 5 | **人物** | 主角 + 对手与配角 |
| 6 | **世界** | 城市背景（视觉基调 / 阶级默认值） |
| 7 | **钩子** | 开场钩（3 秒画面） |

**为什么是这个顺序**：先定"做什么类型"，才能让后面每一步都被收窄；先定结局，才知道需要什么样的人。
顺序反了，用户会在还不知道讲什么的时候被要求选场景，只能瞎点。

---

## 自动补齐的随机性

补齐不是每次都把同一套塞给你——它走**带种子的加权随机**。

| 动作 | 做什么 |
|---|---|
| **🎲 换一批** | 清空 → 换一个种子 → 重抽一套（可复现） |
| **补齐缺口** | 保留已选，只填空位（种子不变，结果稳定） |
| 随机度 `固定/保守/平衡/放开` | 决定从多大的候选池里抽 |

规则：

- **同种子 + 同随机度 = 同结果**。seed 显示在面板上，记下来就能复现那一次。
- **随机但守底线**。不是均匀乱抽，而是「先按质量排序、只在前 K 个里加权抽」：
  随机度低时基本只碰成熟题材包，随机度高才敢试有缺口的包。
- **不脱离题材**。选了「假绿卡」，剧情内核只会从它推荐的 B07 里挑，不会跳到「母爱」。
  随机度 >85% 才允许跳出推荐位。
- **补齐必然合法**。不是"补完再看运气"，而是四道闸一起上：
  1. S 等级硬过滤（`allow_s_levels` 收紧时不补超纲素材）
  2. 内核↔主线按 `pairs_core` 配对抽取，不配对的直接不进池
  3. 簇守门：候选带来的题材簇超过 3 个就重罚，不让它进来
  4. 自查重试：抽完自己 validate 一遍，不干净就换派生种子重抽（≤5 次）

  实测 300 组（60 种子 × 5 档随机度）：**0 阻断、0 提醒**。

```python
bp, log = blueprint.autofill(lib, seed=20261020, randomness=0.6)
```

---

## 候选项按相关性折叠

选完题材后，下面的「剧情内核 / 主线骨架 / 人设」只显示**跟这个题材相关**的：

| 槽位 | 什么算相关 |
|---|---|
| 剧情内核 | 题材包推荐的 |
| 主线骨架 | 题材包推荐的 + 声明了「配当前内核」的 |
| 主角 / 配角 | 与已选内核或主线的标签有交集的 |

不相关的折叠成一行「＋ 显示其他 N 项（与当前题材不搭）」，点开才出现，并标「不搭」。
规则与后端校验一致，前端不会给出后端会拒的选项。

---

## 三个入口

| 入口 | 用途 |
|---|---|
| `server.py` + 浏览器 | 选要素、改人设、看编译预览、生成、读稿、下载 |
| `cli.py` | 脚本化、批量、CI |
| `from core import pipeline` | 嵌进别的流水线 |

```python
from core import pipeline
script = pipeline.run(blueprint, mode="llm", episodes=[1,2,3], progress=print)
print(script["checks"]["passed"], script["files"])
```

---

## 常用命令

```bash
python cli.py list                                        # 有哪些要素 / 槽位 / 风格
python cli.py validate -b examples/blueprint-bag1.json     # 只校验蓝图
python cli.py prompt   -b examples/blueprint-bag1.json -e 1   # 看这一集真实 prompt
python cli.py gen      -b examples/blueprint-bag1.json -m llm -r 1-10
python cli.py gen      -b examples/blueprint-bag1.json -m llm -r 37      # 只重跑第 37 集
python tests/test_pipeline.py                              # 69 项自测
```

---

## 目录

```
core/registry.py     要素类型注册表 —— 加新要素类型只改这里
core/compiler.py     蓝图 → 每集组装卡（爽点梯度 / 场景排布 / 出场控制）
core/renderer.py     组装卡 → prompt → 单集剧本（含降级）
core/validator.py    结构 / 引用 / 规则 三层校验
core/pipeline.py     五阶段编排
data/elements/       要素包（自动生成，勿手改）
data/styles/         风格包 —— 加新风格只加 JSON
examples/            示例蓝图
```

---

## 改动素材库之后

`material-library/` 仍是唯一事实源。改完重跑：

```bash
python tools/build_elements.py --market MX
```

**不要手改 `data/elements/*.json`** —— 下次编译会被覆盖。
