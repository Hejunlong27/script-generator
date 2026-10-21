# 剧本生成器 · 架构与落地方案

> 接管 `material-library` 后的完整设计。代码在 `script-generator/`，本文说明**为什么这么建**。

---

## 0. 一页结论

| 项 | 结论 |
|---|---|
| 原项目是什么 | 一套**写给人和 LLM 读的素材库**：412 条素材，Markdown 表格 + 2 个 JSON Schema，**零代码** |
| 最大的机会 | 素材本身是对的，缺的是「机器可组装的接口」——ID 有了，但没有可执行的引用图和装配器 |
| 最大的风险 | 让 LLM 直接决定结构。30 集会散架，而且错在哪查不出来 |
| 本方案的核心主张 | **结构由代码定，文字由模型写。** 编译器先产 60 张组装卡（确定性），LLM 只填肉 |
| 拿到的保证 | 输出结构统一 · 要素引用一致 · 爽点梯度合规 · 成本可控 · 无 key 也能出稿 |
| 目标规格 | **30 集 × 60 秒**（可改，风格包可声明自己的默认规格） |
| 代码规模 | 约 4.3k 行 Python（标准库，零第三方依赖）+ 1 个单页前端 |

---

## 1. 现有项目梳理

### 1.1 资产盘点

```
material-library/
├── README.md                      架构规划 v1（原则/分层/市场/配额/风险）
├── schema/
│   ├── material.schema.json       素材条目结构（12 种 type，S0–S3 等级）
│   └── episode.schema.json        单集组装卡结构（时间轴 5 段 + 成本 + 合规）
└── mx/                            墨西哥 v1，唯一已建市场
    ├── 00-market.md               L0 底座 + 7 条红线 + narco 替代方案
    ├── 01-genres.md               45 个平台标签映射（A/B/C/D 级）+ 8 个组合包
    ├── 02-scenes-objects.json     35 场景 + 49 物品（含 cast/props/fits 字段）
    ├── 03-hooks-beats.json        45 钩子（开场/集尾/转折）+ 31 爽点（刻度 1–10）
    ├── 04-characters.json         12 张人设卡（含「不可做」清单）
    ├── 05-dialogue.md             称谓系统 + 西语爆点句 + 阶层标记 + 禁忌词
    ├── 06-arc.md                  60 集五段主线 + 3 集完整组装卡（试跑验证）
    ├── 07-dialogue-scenes.md      14 个高频场景 × 台词绑定
    ├── 08-subplots.md             4 类 × 8 条支线 + 60 集排布图
    ├── 09-package-check.md        8 个组合包逐包验证
    └── picker/                    ★ 选择题层：8 模块 × 30 项 = 240 项
        ├── 01-city.md   A01–A30  城市背景（选 1）
        ├── 02-core.md   B01–B30  剧情内核（选 1）
        ├── 03-arc.md    C01–C30  主线骨架（选 1）
        ├── 04-hero.md   D01–D30  主角人设（选 1）
        ├── 05-rival.md  E01–E30  对手与配角（选 2）
        ├── 06-conflict.md F01–F30 冲突内核（选 3，三层齐）
        ├── 07-hook.md   G01–G30  开场钩子（选 1）
        └── 08-ending.md H01–H30  结局走向（选 1）
```

**关键发现：`picker/` 是整个项目真正的接口层。** 它已经把「一部短剧」拆成了 8 个必选项 + 一套兼容规则（五簇、簇间矩阵、三条硬规则、五项校验）。这不是素材，这是**表单**。本方案的蓝图（Blueprint）就是它的机器可读版本。

### 1.2 实际技术栈

原项目**没有任何代码**：

| 现在有的 | 现在没有的 |
|---|---|
| Markdown 表格（人读） | 可执行的引用关系 |
| 2 个 JSON Schema（结构定义，无实例校验器） | 数据实例（schema 有，数据没有） |
| 一套写给人看的拼装规则（README §2.3、"五项校验"） | 规则的代码实现 |
| 3 集手工组装的 YAML 卡（在 `06-arc.md` 里） | 批量生成组装卡的算法 |
| — | 任何 LLM 调用、校验、导出代码 |

所以"接管"不是改造，是**在素材库之上补一层可执行层**。

### 1.3 可复用 / 需新建

| 资产 | 判定 | 怎么用 |
|---|---|---|
| `picker/` 240 项 | ✅ 直接复用 | 编译成 `data/elements/MX.elements.json`（412 条），作为唯一要素源 |
| `02/03/04.json` 的 JSON 数据 | ✅ 直接复用 | 同上；`cast`/`fits`/`need`/`lvl`/`never_do` 全部进 `attrs`，一条不丢 |
| `material.schema.json` 的 ID 规则 | ✅ 复用并扩展 | 原规则 `{MKT}-{TYPE}-{NNN}`；扩展为允许字母码（`C01`）以兼容 picker |
| `episode.schema.json` | ✅ 复用为组装卡规范 | 编译器输出严格遵循它的 `timeline` 五段 / `cost` / `compliance` |
| `05-dialogue.md` 称谓系统 | ✅ 半结构化复用 | 提炼为**语域时间轴**（`usted→tú→usted`），编译期就写进每集卡 |
| `00-market.md` 红线表 | ✅ 结构化复用 | 解析为 `market_base.attrs.red_lines`，做两轮关键词扫描 |
| `06-arc.md` 五段式 | ✅ 复用为风格包骨架 | 抽成 `styles/mx-telenovela.json` 的 `segments`，比例可改 |
| `08-subplots.md` / `09-package-check.md` | ⏸ 暂不接入 | 支线挂载需要 `episode→subplot` 映射，第二阶段做（见 §9） |
| — | 🆕 新建 | 注册表 / 编译器 / 校验器 / 渲染器 / LLM 适配 / 导出 / UI |

### 1.4 原设计的三个结构性缺口

这三条是接管后才看清的，也是本方案要正面解决的：

1. **规则在文档里，不在代码里。** "F 必须三层齐""B∩C≥1""簇不超过 3"——靠人记。第 40 集崩了才知道当初拼错了。→ 变成 `Blueprint.validate()`，拼错当场报错。
2. **schema 有，数据没有。** `episode.schema.json` 定义了组装卡，但只存在 3 张手写 YAML。30 集没法手写。→ 变成 `Compiler`，0.05 秒产 30 张卡。
3. **ID 存在，引用图不存在。** 素材之间有 `need`/`fits`/`visual_symbol`/`pkg` 等隐式关系，但没有一处代码能回答"这个场景能不能承载这个爽点"。→ 变成 `attrs.required_refs` + 打分函数 + 校验器。

---

## 2. 技术栈选型

| 层 | 选型 | 理由 |
|---|---|---|
| 运行时 | **Python 3.13，仅标准库** | 素材是文本 + JSON，没有计算密集环节；零依赖 = 换机器不用装环境，也不会有版本地狱 |
| HTTP 服务 | `http.server.ThreadingHTTPServer` | 4 个接口，单机自用；不引 FastAPI/uvicorn |
| 前端 | 单页 HTML + 原生 JS，无构建 | 表单 + 列表 + 预览，框架是负担 |
| 存储 | **文件即数据库**（JSON） | 要素库、蓝图、成品都是 JSON，可 diff、可版本管理、可直接给下一个环节 |
| LLM | **OpenAI 兼容协议**（`urllib` 直连） | 一套代码通吃 OpenAI / DeepSeek / 通义 / 智谱 / 本地 vLLM / Ollama，只改 `base_url` |
| 并发 | `ThreadPoolExecutor` | 瓶颈是网络等待，不是 CPU；GIL 无影响 |

**没有引入的东西及原因**：数据库（要素是只读配置，成品是文件）、向量检索（要素是 412 条精选，穷举打分比 embedding 更准更可控）、LangChain 类框架（会把 prompt 和错误处理藏起来，排查成本远高于手写 200 行）。

---

## 3. 核心数据模型

### 3.1 四层结构

```
Element（要素 · 只读素材）        412 条，来自 material-library
   │  用户选择 / 编辑 override
   ▼
Blueprint（蓝图 · 用户预设）      一剧一份，可存盘、可分享、可复现
   │  Compiler（确定性，不调模型）
   ▼
Card（组装卡 · 每集一张）          60 张，槽位全部填好、引用全部锁定
   │  Renderer（LLM 或模板）
   ▼
Script（剧本 · 交付物）            30 集正文 + 账本 + 审计
```

**分层的意义**：任何一层出问题，错误都在该层暴露，不会传染。要素选错了在 Blueprint 层报错；结构不合理在 Card 层可查；文字不好在 Script 层重跑单集，不动前面。

### 3.2 Element —— 统一要素结构

所有 12 种要素共用一个外壳，类型差异放 `attrs`：

```jsonc
{
  "id": "MX-CHR-D01",          // 唯一编码，引用只认这个
  "type": "character",          // 类型 key（注册表决定）
  "type_cn": "主角人设",
  "market": "MX",
  "layer": "L2",                // L0 底座 / L1 现实素材 / L2 叙事要素
  "name": "庄园女佣",
  "name_local": "",             // 本地语言叫法（西语场景名/物名必填）
  "s_level": "S1",              // S0 直用 / S1 换皮 / S2 重写 / S3 禁区
  "tags": ["CLA", "IDE"],       // 10 个题材标签，兼容性判定靠它
  "attrs": {                    // 类型专有字段，原库字段一个不丢
    "identity": "庄园女佣",
    "surface": "温顺、做事实在",
    "flaw": "别人给东西，她先退回去",
    "secret": "她才是真继承人"
  },
  "source": "picker/04-hero.md#D01"   // 回溯到素材库原文
}
```

**为什么是 `attrs` 而不是一堆强类型子结构**：schema 本来就有 12 种 type，每种字段不同。强类型会让"加一个新类型"变成"改 12 个地方"。现在加类型只需要动注册表一行 + 补数据。

### 3.3 ID 规范

```
{MARKET}-{TYPE}-{LOCAL}

MX-SCN-001     主库 JSON 的数字序号（三位）
MX-ARC-C01     picker 的字母码（字母 + 两位）
```

正则：`^[A-Z]{2}-[A-Z]{3}-(?:[A-Z]?\d{2,3})$`

保留原库规则（三位数字），同时接受 picker 的字母码——**不重编 ID**，因为原库文档、`fits`、`need`、`visual_symbol` 里到处都是旧 ID，重编会拆掉所有交叉引用。

### 3.4 四类核心要素的字段

#### 主线 `arc`（30 选 1）

| 字段 | 说明 |
|---|---|
| `attrs.skeleton` | **事件链**，不是散文大纲：`起点 → 中点反转 → 终点` |
| `attrs.structure_type` | 上升/下降/反转/交易/对撞/群体（从 `03-arc.md` 的结构分布表解析） |
| `attrs.pairs_core` | 作者标注的配内核（`B03`），用作一致性提醒 |
| `tags` | 决定 C∩D 链式校验 |

选它的三条硬约束（代码执行）：与 `core` 至少共享 1 个标签；与 `hero` 至少共享 1 个标签；本身不承载集数（集数由风格包的 `segments.ratio` 决定——同一骨架可跑 30 集也可跑 90 集）。

#### 人设 `character`（1 主角）+ `support`（1–2 配角）

主角来自 picker D 层（身份/表面/缺陷/秘密），配角来自 E 层（身份/槽位/关系/不可做）。合并后统一成：

| 字段 | 来源 | 用途 |
|---|---|---|
| `identity` | D/E | 身份，进 prompt 与人物表 |
| `surface` / `flaw` / `secret` | D | 人设三层，`flaw` 必须可演 |
| `slot` / `relation` | E | 功能槽位（裁判者/掠夺者/背叛者…） |
| `never_do` | D（04.json）/E | **人设不崩的唯一硬手段**，进 prompt + 生成后启发式扫描 |
| `speech` | 04.json | 称谓变化规划（"称 usted 至第 18 集"） |
| `visual_symbol` | 04.json | 关联 OBJ，编译期优先塞进该角色的集数 |
| `function_arc` | 04.json | 功能位移：阻碍者→镜像→同盟 |

**人设是唯一会生成"新名字"的要素。** 名字由 `data/names/MX.json` 的西语名池按 `hash(seed, element_id)` 确定性分配（含中文音译），并写入 `id_ledger`。同一蓝图重跑，名字不变。

#### 场景 `scene`（35 条池，不手选）

| 字段 | 用途 |
|---|---|
| `attrs.cast` | 可拍人数上限 —— **成本的唯一硬指标**，直接进过滤 |
| `attrs.ai_difficulty` | 由 `cast` 推导（≥4 人 = 高），低成本集避开 |
| `attrs.light` | 光线（日/夜/晨昏），进 prompt 与场头 |
| `attrs.props` | 场景自带道具 → 与 `prop` 库做名字匹配 |
| `attrs.fits` | 能承载的桥段（对峙/求婚/揭穿…）→ 与爽点 kind 匹配打分 |
| `attrs.class` | 阶级属性（老钱/贫困…）→ 与主角标签匹配 |

**为什么场景不手选**：35 个场景 × 30 集是排布问题，不是选择题。用户手选 30 次不现实，且人会不自觉重复用 3 个场景。

编译器的场景打分用的是**布景摊销**逻辑（这是短剧的真实成本结构，不是"多样性优先"）：

```
用过 1–3 次 → -2   （景搭好了，反复用才便宜）
第 4 次起   → +(n-3)×5 （用太多次会单调）
上一集刚用过 → +6    （只避相邻重复，不避跨集复用）
题材包指定场景 → -5  （优先用该题材的核心场景）
```

实测：30 集用 10 个景，60 集用 18 个景。**景越少越省，但不能少到单调。**

#### 支撑要素

| 类型 | 数量 | 编译期角色 |
|---|---|---|
| `core` 剧情内核 | 30 | 全剧落点，进每集 prompt 与 logline |
| `conflict` 冲突内核 | 30 | **必须 3 个、三层齐**；按集轮换，提供"具体演法"（可拍动作） |
| `hook` 钩子 | 75 | 开场钩（蓝图指定 1 个 + 自动补）、集尾钩（按段位自动配） |
| `beat` 爽点 | 31 | 带刻度 1–10，按梯度铁律排布 |
| `ending` 结局 | 30 | 终局承诺，写进每集 `hook.promise` |
| `city` 城市 | 30 | 视觉基调 + 阶级默认值 |
| `prop` 物品 | 49 | 符号物/伏笔，按场景道具与角色视觉符号配对 |
| `package` 题材组合包 | 8 | **第一道题**：标签组合 + 卖点 + 核心钩子 + 推荐 core/arc + 场景清单 + 可拍性 |
| `genre` 平台题材标签 | 45 | 不进槽位：A/B/C/D 四级风险标注，用于给题材包打标签 |

### 3.5 要素之间的关联方式（6 种边）

```
                  ┌────────── core（欲望） ──────────┐
                  │ tags 交集 ≥1                      │ tags 交集 ≥1
                  ▼                                   ▼
                arc（骨架） ──────────────────── character（主角）
                  │ skeleton 三段                     │ visual_symbol
                  │                                   ▼
         ┌────────┴────────┐                        prop
         │ segment ratio   │                          ▲
         ▼                 ▼                          │ props 名字匹配
      episode 段位 ──→ conflict（3 层）                │
         │                 │ staging（可拍动作）       │
         │                 ▼                          │
         ├──→ beat（kind,lvl） ←──fits── scene ───────┘
         │                          ▲  cast≤预算, light
         ├──→ hook（kind, need）     │
         │                          └── city（视觉基调）
         └──→ support（slot, relation, never_do）
```

| 关系 | 表达位置 | 校验方式 |
|---|---|---|
| **兼容**（tags 交集） | `core.tags ∩ arc.tags`、`arc.tags ∩ hero.tags` | `IN_COMPATIBILITY` 阻断 |
| **组成**（段位→集） | `style.segments.ratio` → 每集 `arc_segment` | 集数连续、无空档 |
| **承载**（场景能否承载爽点） | `scene.attrs.fits ∋ beat.kind` | 打分优先，不阻断 |
| **依赖**（钩子/爽点需要哪些素材） | `attrs.required_refs`（原 `need`） | 引用必须在库内 |
| **象征**（角色↔物品） | `character.attrs.required_refs`（原 `visual_symbol`） | 物品进该角色集数的 `props` |
| **层级**（冲突三层） | `conflict.attrs.layer ∈ {外部,内部,结构}` | `IN_INCOMPATIBLE` 阻断 |

### 3.6 引用一致性：`id_ledger`

这是"要素引用一致"的执行机制，也是我认为整个方案里最该被复用的一处设计。

**问题**：LLM 每集独立生成，第 3 集把女主叫"埃莱娜"，第 27 集可能叫"海伦娜"；场景 ID 抄错一位；道具换了名字。

**解法**：编译期生成一份**账本**，把"要素 ID → 剧中显示名"一次性定死，然后：

1. 账本随每集 prompt 下发（人物卡直接写"希梅娜·贝尔特兰（Ximena Beltrán）｜ID=MX-CHR-D01"）
2. 生成后校验器比对：`lines[].speaker` 必须等于账本里的 `name_zh`，否则 `VAL_NAME_DRIFT`
3. 全局扫描：正文里出现的每个 `MX-*-*` ID 必须在要素库存在，否则 `VAL_REF_UNKNOWN`
4. 剧本头部的"要素配置表"直接由账本生成，人读也能核对

```jsonc
"ledger": {
  "characters": {
    "MX-CHR-D01": {"name_es":"Ximena","name_zh":"希梅娜",
                   "full_zh":"希梅娜·贝尔特兰","full_es":"Ximena Beltrán",
                   "role":"主角","never_do":["不主动索要…","不在人前哭…"]}
  },
  "scenes": {"MX-SCN-003": {"name":"廉价公寓","local":"vecindad"}},
  "props":  {"MX-OBJ-018": {"name":"婚纱","local":"vestido de novia"}}
}
```

---

## 4. 输入格式

### 4.1 Blueprint JSON

```jsonc
{
  "schema_version": "1.0",
  "meta": {
    "title": "顶替新娘",
    "market": "MX",                  // 决定载入哪个要素包
    "episode_count": 60,             // 1–120
    "episode_seconds": 90,
    "style": "mx-telenovela",        // data/styles/*.json 的 id
    "output_language": "zh",         // 说明/动作中文，台词西语+意译
    "seed": 20261020                 // 编译与取名确定性
  },

  "elements": {                      // 9 个槽位，按 step 顺序
    "package":   "MX-PKG-001",       // ① 题材组合包（8 选 1）——先定这个
    "city":      "MX-LOC-A30",
    "core":      "MX-COR-B03",
    "arc":       "MX-ARC-C01",
    "character": "MX-CHR-D01",       // 也可写 hero，注册表有别名
    "support":   ["MX-SPT-E01", "MX-SPT-E02"],
    "conflict":  ["MX-CFT-F01", "MX-CFT-F04", "MX-CFT-F03"],
    "open_hook": "MX-HOK-G01",
    "ending":    "MX-END-H15"
  },

  "overrides": {                     // 可选：就地改要素，不污染素材库
    "MX-CHR-D01": {
      "name": "庄园女佣·埃莱娜",
      "attrs.flaw": "别人给东西，她第一反应是退回去",
      "attrs.never_do": ["不主动索要任何东西", "不在人前哭"]
    }
  },

  "constraints": {                   // 可选：全部有默认值
    "max_cast_per_scene": 3,
    "max_crowd_scenes": 3,
    "max_scene_per_episode": 2,
    "allow_s_levels": ["S0", "S1", "S2"],
    "auto_fill": true,
    "forbidden_topics": ["毒枭", "总统", "选举"],
    "beat_level_min": 1,
    "beat_level_max": 10
  }
}
```

### 4.2 槽位规则（代码执行，不是建议）

**顺序即认知顺序**——`core/registry.py` 的 `step` 字段决定界面呈现次序：

| 步 | 槽位 | 数量 | 硬约束 |
|---|---|---|---|
| 1 | `package` 题材组合包 | 1 | 8 选 1。决定后面所有候选项的排序与推荐 |
| 2 | `core` 剧情内核 | 1 | `core.tags ∩ arc.tags ≥ 1` |
| 3 | `arc` 主线骨架 | 1 | `arc.tags ∩ character.tags ≥ 1` |
| 4 | `conflict` 冲突内核 | **3** | **必须齐 外部 / 内部 / 结构** |
| 5 | `ending` 结局走向 | 1（可选） | 不选则按内核兜底 |
| 6 | `character` 主角人设 | 1 | S 等级在 `allow_s_levels` 内 |
| 7 | `support` 对手与配角 | 2（≥1） | 至少 1 个出自 E01–E15（对手槽位） |
| 8 | `city` 城市背景 | 1 | 类型必须 `city` |
| 9 | `open_hook` 开场钩子 | 1 | 类型必须 `hook`，kind=开场钩 |

**为什么题材在最前**：题材不是"又一个素材"，它是**筛选器**。选完「顶替新娘」，后面的剧情内核、
主线骨架、人设候选都会被打上「推荐」标记并优先排序（数据来自两处：
`package.attrs.preset_cores/preset_arcs` 是人工标注的推荐位，`arc.attrs.pairs_core` 是作者标注的内核配对）。

选完题材会自动预填 `core` 与 `arc`（只在空位时填，不覆盖用户已选），并提示"可随时改"。

**关于「剧情内核 vs 主线骨架是否重复」**——不重复，但强绑定，所以不让人分别做两道题：

| | 回答什么 | 例（包 1） | 用途 |
|---|---|---|---|
| `core` | 这部剧在讲**什么欲望** | 她替姐姐嫁进这个家，第八年才发现当初该嫁的人是她 | 卖点、投放文案 |
| `arc` | 这条欲望**怎么走完** | 女佣顶替进门 → 才发现自己才是真继承人 → 把位置让出去 | 分集结构 |

一个是主题，一个是结构。`arc.attrs.pairs_core` 已经记录了作者的配对关系，
所以运行时由题材包带出默认值，用户只在"想换骨架"时才展开改。

### 4.3 自动补齐：带种子的加权随机

自动补齐一开始是硬排序（永远选最优），结果每次点都是同一套——那不是"稳妥"，是"没得选"。
现在改成**先按质量排序、再在前 K 个里加权抽样**：

```python
k = round(1 + randomness * (n - 1))        # 随机度决定池子大小
w_i = ((k - i) / k) ** (1 / randomness)    # 池内按排名衰减
```

| randomness | 行为 |
|---|---|
| 0 | k=1，永远最优（确定性，可复现） |
| 0.6（默认） | 在 top 60% 里抽，头部占优 |
| 1 | 全池线性权重 |

**三条不能破的底线**（随机不等于胡闹）：

1. **只在前 K 个里抽**。否则「缺口 5 个场景的包」和「已验证的包」等概率，那不是随机。
2. **core/arc 锁在题材推荐位内**。随机度 <0.85 时只在 `preset_cores/preset_arcs` 里抽；
   否则等于"选了题材却拍了另一部剧"。
3. **兼容过滤必须用校验的同一规则**。校验比的是 `core.tags ∩ arc.tags`（B∩C）和
   `arc.tags ∩ character.tags`（C∩D）——过滤池子必须用同样的两个集合，
   不能用累积的 ref_tags（那会让"和冲突沾边"的人设混进来，然后必然报断链）。
   这一条踩过坑：40 种子 × 5 档随机度里出现过 `C12(SUP/REL) + D01(CLA/IDE)` 的非法组合。

两层动作分开：**换一批**（清空+换种子重抽）和 **补齐缺口**（保留已选，只填空白）。
合成一个按钮就会出现"补齐过一次后再点没反应"——槽位都满了，没什么可补。

#### 配对：不要让随机决定连导演都不认的组合

素材库里 `arc.attrs.pairs_core` 已经标了「这条骨架配哪个内核」。
一开始我在组合包数据里**又手写了一份** `preset_arcs`，结果 8 个包里有 4 个对不上
（包 5 的 C15 实际配 B16、包 6 的 C05 配 B06、包 7/8 顺序错位），
随机补齐就会冒出 `IN_PAIRING` 黄条。

修法是**消灭第二份事实**：组合包只标 `cores`，`preset_arcs` 由 `pairs_core` 反查生成。
补齐时先抽内核、再只从「声明配这个内核」的主线里抽。这样配对是构造出来的，不是校验出来的。

同一条原则应用到簇：候选带来的新簇若会让总数超过 3，直接重罚（×100），不让它进池。

最后还有一道兜底：**抽完自己 validate 一遍**，若还有 error / `IN_PAIRING` /
`IN_CLUSTER_*`，换派生种子重抽（≤5 次，纯计算约 1ms）。
四道闸叠起来，实测 300 组 0 阻断 0 提醒。

> 顺带清掉一类噪音：`auto_fill` 开着时，`IN_AUTOFILL`「未选将自动补齐」不该报——
> 缺口一定会被补上，报出来只是让人以为有问题。现在改成补齐后重算一次校验结果。

### 4.4 覆盖机制（用户编辑预设要素）

三种改法，代价从低到高：

1. **换一项**：直接改 `elements` 里的 ID。
2. **改一项**：写 `overrides[id]`。不改素材库本体，只作用于本次蓝图；未改的字段从素材继承。
3. **加一个要素类型**：见 §8.1。

---

## 5. 生成流程

```
┌─ INPUT ────────────────────────────────────────────────────┐
│ 载入要素包 / 风格包 / 蓝图                                  │
│ validate()  ── 阻断型问题在这里暴露，**不消耗任何模型额度**   │
│ autofill()  ── 缺口按标签相似度补齐，记录在 autofilled       │
└────────────────────────────┬───────────────────────────────┘
                             ▼
┌─ COMPILE（确定性，0.05 秒 / 30 集）─────────────────────────┐
│ ① 人名账本   ② 五段切分   ③ 爽点梯度   ④ 逐集排布            │
│    每集产出：timeline 5 槽 / cast / scene_refs / props /     │
│    beat / hook / 语域 / compliance / cost / id_ledger        │
└────────────────────────────┬───────────────────────────────┘
                             ▼
┌─ RENDER ＋ VALIDATE（每集独立，可并发）─────────────────────┐
│ render → validate → 有 error？→ repair（回喂问题，≤2 次）    │
│                   → 仍失败？→ 降级模板稿（结构必然合规）      │
└────────────────────────────┬───────────────────────────────┘
                             ▼
┌─ ASSEMBLE ＋ EXPORT ───────────────────────────────────────┐
│ 全剧校验（缺集 / 同名不同人 / 未知 ID）                      │
│ md · html · json · csv · fountain                          │
└────────────────────────────────────────────────────────────┘
```

### 5.1 编译算法（这是"结构统一"的来源）

**① 五段切分**：按风格包的 `segments.ratio` 分配集数并修正取整误差。`mx-telenovela` 的比例是
0.133/0.267/0.233/0.2/0.167 —— **30 集时切出来是 4/8/7/6/5，60 集时是 8/16/14/12/10**。
同一骨架可以跑 12 集也可以跑 90 集，比例不变。

**② 爽点梯度**（`beat_ladder`）：四条铁律同时满足，且是纯函数（同种子同结果）：

| 铁律 | 实现 |
|---|---|
| 相邻两集刻度差 ≤ 2 | 双向夹逼（min 收敛） |
| 每 10 集至少 1 个 ≥ 7 | 每块铺锯齿峰值；未达标则抬峰并**向前铺 ramp**，避免制造新跳变 |
| 9–10 档全季 ≤ 3 次 | 超出则降为 8 |
| 1–3 档不得连续 > 4 集 | 超限则 +1 打断 |

实测 30 集输出：`2 2 3 4 5 6 6 7 5 4 | 3 3 4 5 6 7 6 7 5 4 | 3 3 4 5 6 7 8 9 7 6`，四项全过。

**③ 场景排布**（打分，非随机）：

```python
key = 8  · (群体超预算)
    + 布景摊销（1–3 次 -2，第 4 次起 +5）   # 景要复用，但不能单调
    + 6  · (上一集刚用过)
    - 3  · (场景 tags ∩ 主角/内核 tags)
    - 3  · (爽点 kind ∈ 场景 fits)
```

过滤：`s_level ∈ allow`、`cast ≤ max_cast_per_scene`。高潮集（刻度 ≥ 6）允许换景一次（`max_scene_per_episode`）。

**④ 出场控制**：主角必在场；低成本集（刻度 ≤ 4）预算 2 人，高潮集 3 人；其余配角降级为「仅提及」或「声音」——这是原库反复强调的省钱手段，现在是代码。

**⑤ 语域时间轴**：`usted`（段1–2前2/3）→ **转折集切 `tú`**（段2后1/3）→ 段3 `tú` → **段4开头退回 `usted` = 决裂** → 段5 回到 `tú`。把原库"语言即权力"这条洞察变成每集的 `dialogue_notes.address_shift`。

### 5.2 渲染（prompt 结构）

Prompt 完全由**风格包 JSON** 定义（`system_prompt` + `episode_prompt` 模板 + `output_skeleton`），代码只做槽位填充：

```
system: 你是墨西哥竖屏短剧编剧…只输出 JSON…
        【6 条铁律：人物只能用给定名字 / 不得违反 never_do / 只引用给定 ID /
         西语口语+中文意译 / 严格 5 段时间轴 / 不碰红线】

user:   【全剧设定】内核 / 主线 / 城市 / 对手 / 三层冲突 / 结局承诺
        【本集】第 N 集 · 段位 · 本段任务 · 本集需推进
        【五段时间轴】0-3s open_hook［MX-HOK-G01］她在自己婚礼上撕掉身份证…
                     15-50s escalation［MX-CFT-F01］全家人轮流来劝，每次都带礼物
                     50-75s reversal［MX-BEAT-010］lvl=2 沉默碾压：…
        【人物卡】希梅娜·贝尔特兰（Ximena Beltrán）｜ID=MX-CHR-D01｜主角
                 缺陷：…  秘密：…  不可做：1) … 2) …
        【可用场景】[MX-SCN-003] 廉价公寓（vecindad）：夜；可拍 2 人
        【可用物品】[MX-OBJ-018] 婚纱（vestido de novia）
        【语域】敬语；本集称谓变化：全片一律 usted…
        【前情提要】第 N-1 集《…》：… ｜状态：…
        【输出 JSON】{…骨架…}
```

关键点：**prompt 里既有"必须做到"（五段时间轴照抄），也有"不能做"（never_do / 红线），还有"只能用这些"（ID 白名单）**。三者叠加，模型的可自由发挥空间被压缩到"怎么写得好"，而不是"写什么"。

### 5.3 校验-修复环

```
render ──→ validate ──┬─ 无 error ──→ 收
                      └─ 有 error ──→ repair(带问题清单+上一稿) ──→ validate
                                        └─ 仍 error（≤2 次）──→ 降级模板稿
```

`repair` 的 prompt 只多两段：问题清单（`[VAL_REF_SCENE] 场景 MX-SCN-999 不在可选列表`）+ 上一稿全文，要求"逐条修正，其余不动"。**问题清单是可定位的（带 `where` 路径），所以修复是收敛的**——这比"再生成一次"有效得多。

---

## 6. 输出格式

### 6.1 Script JSON（主交付物）

```jsonc
{
  "schema_version": "1.0",
  "meta": { title, market, episode_count, episode_seconds, style,
            elements, constraints, generated_at, mode, episodes_generated, elapsed_sec },

  "plan": {                          // 全剧级审计
    "segments":  [{name, start, end, goal}],
    "beat_ladder": [2,2,3,4,5,6,6,7,...],
    "beat_ladder_check": {max_adjacent_delta, rule_adjacent, min_peak_per_10,
                          rule_block_peak, count_9_10, rule_high_cap,
                          max_low_run, rule_low_run},
    "register_timeline": [{episode, register, note}],   // 称谓切换点
    "cost_summary": {distinct_scenes, crowd_scenes, avg_cast, distinct_props},
    "element_usage": {scenes, beats, props}             // 每个要素用了几次
  },

  "ledger": { characters, scenes, props, hooks, beats },   // 引用账本
  "characters": [ {full_zh, full_es, role, identity, flaw, never_do, ...} ],

  "episodes": [{
    "episode": 1,
    "title": "E01 沉默碾压·廉价公寓",
    "logline": "…",
    "arc_segment": "契约绑定",
    "segments": [ {range:"0-3s", kind:"open_hook", text:"…"}, … 5 段 ],
    "scenes": [{
      "slug": "1-1", "scene_ref": "MX-SCN-003", "location": "廉价公寓",
      "time": "夜", "cast_refs": ["MX-CHR-D01","MX-SPT-E01"], "camera": "近景对峙",
      "action": "…",
      "lines": [ {"speaker_ref":"MX-CHR-D01", "speaker":"希梅娜",
                  "text":"¿Y esto?", "gloss":"这是什么？", "register":"敬语"} ],
      "props": ["MX-OBJ-018"]
    }],
    "beat": {"ref":"MX-BEAT-010", "kind":"沉默碾压", "level":2, "info_gap":"…"},
    "hook": {"ref":"MX-HOK-016", "promise":"…"},
    "compliance": {"passed":true, "hits":[]},
    "_checks": {"passed":true, "errors":0, "warnings":0, "issues":[…]},
    "generator": {"mode":"llm|template", "degraded":false, "model":"…"}
  }],

  "checks": {passed, errors, warnings, issues},   // 全剧级
  "failures": [],                                  // 失败集（部分交付时看这里）
  "files": {md, html, json, csv, fountain}
}
```

**每一集都自带 `_checks` 与 `generator`**：下游能一眼看出哪一集是 LLM 写的、哪一集是降级的、哪一集有未解决问题。不藏问题。

### 6.2 四种导出

| 格式 | 用途 | 特点 |
|---|---|---|
| `.md` | 人读 / 进 Notion、飞书 | 要素表 + 人物卡 + 分集大纲 + 分场剧本 + 制作口径 |
| `.html` | 交付 / 审阅 | 单文件无依赖、可打印、直接发给人看 |
| `.json` | 进下游流水线 | 配音、分镜、AI 生成都从这里取结构化数据 |
| `.csv` | 进表格 / 报价 / 排期 | 一行一个镜头，含说话人、台词、意译、道具、爽点刻度 |
| `.fountain` | 进影视工具 | Highland / Fade In 可直接打开，`INT./EXT.` 场景头自动生成 |

---

## 7. 错误处理策略

### 7.1 分四层，处置方式不同

| 层 | 错误码 | 处置 | 是否消耗额度 |
|---|---|---|---|
| 输入 | `IN_BLUEPRINT` `IN_REF_NOT_FOUND` `IN_TYPE_MISMATCH` `IN_CARDINALITY` `IN_INCOMPATIBLE` `IN_COMPATIBILITY` `IN_CONSTRAINT_BLOCKED` | **阻断**，返回 issue 列表（含 `where`/`hint`），前端直接显示 | 否 |
| 生成 | `GEN_NO_LLM` `GEN_LLM_FAILED` `GEN_LLM_TIMEOUT` `GEN_LLM_RATE_LIMIT` `GEN_LLM_BAD_RESPONSE` | **重试 + 降级** | 是 |
| 校验 | `VAL_SHAPE` `VAL_SEGMENTS` `VAL_REF_SCENE` `VAL_REF_CAST` `VAL_REF_SPEAKER` `VAL_REF_BEAT` `VAL_REF_HOOK` `VAL_REF_UNKNOWN` `VAL_NAME_DRIFT` `VAL_REDLINE` `VAL_CHARACTER` | **回喂修复 ≤2 次**，仍失败降级 | 是 |
| 系统 | `SYS_LIBRARY` `SYS_EXPORT` `SYS_NOT_FOUND` `SYS_FORBIDDEN` | 直接抛出，界面明示 | 否 |

### 7.2 配置来源与优先级

```
环境变量 SG_LLM_*  >  config.local.json（界面里填的）  >  代码默认值
```

- 环境变量优先，是为了让 CI / 容器部署能覆盖本地文件
- 界面里保存的配置落到 `config.local.json`，已在 `.gitignore`；重启后仍生效
- Key 永不回传页面：`describe()` 只输出 `***尾号4位`；前端输入框已配置时显示"留空表示不改"
- 传 `"***xxxx"` 形式的占位串**不会**覆盖已存的真实 key（防误覆盖）
- `POST /api/config/test` 用一次 `max_tokens=32` 的最小调用验证连通性，不抛异常，返回结构化结果

### 7.3 重试策略（按错误类型分治）

| 错误 | 重试 | 手法 |
|---|---|---|
| 429 限流 | 是，≤3 次 | 指数退避 `min(20, 2^n · 1.2) + jitter`；生产上应降到 `workers=1~2` |
| 5xx | 是，≤3 次 | 同上 |
| 超时 | 是，≤3 次 | 调大 `SG_LLM_TIMEOUT` 或缩短单集 |
| 401/403 | **否** | 立刻中止，提示检查 key——重试只会浪费 3 次配额 |
| 400 + `response_format` | 是，1 次 | 自动关掉 json_mode 重发 |
| JSON 坏 / 截断 | 是（校验层） | 走 repair；截断则提示调大 `SG_LLM_MAX_TOKENS` |
| 内容过滤拦截（空返回） | 否 | 记 `GEN_LLM_BAD_RESPONSE`，该集降级 |

### 7.4 降级阶梯（关键：永远能出稿）

```
1. LLM 首次生成
2. 校验失败 → repair（带问题清单，≤2 次）
3. 仍失败 → 模板稿（template_episode，从组装卡 seeds 直接成文）
       └─ 结构、引用、语域必然合规；`generator.degraded = true` 明示
4. 模板也失败（理论上不会） → 记入 failures[]，其余集照常交付
```

**为什么把降级做成设计的一部分**：短剧的实际生产是"先有结构，再润色文字"。没有 API key 时，一个结构正确、台词粗糙的 30 集初稿，价值远高于"生成失败"。老板可以先看骨架对不对，再决定值不值得上模型。

无 key 实测：**30 集 × 60 秒**全流程 **0.05 秒**，0 阻断，10 个场景、49 个道具、爽点梯度四项全过。

### 7.5 部分失败

任务不因单集失败而整体失败：`failures[]` 记录失败集与错误码，`checks` 照常汇总，其余集正常导出。UI 里失败集标红，可只重跑该集（`episodes: [37]`）。

---

## 8. 扩展指南

### 8.1 加一个新的要素类型

以"桥段 TROPE"为例，**只改 3 处，不用动校验器 / 导出 / 前端**：

1. `core/registry.py` 的 `ELEMENT_TYPES` 加一条（**`step` 决定它在界面上排第几步**）：
   ```python
   "trope": TypeSpec("trope", "TRP", "桥段", "L2", role="multi", pick=2,
                     stage="2 剧情 · 讲什么、怎么走", step=35,
                     text_field="attrs.text", hint="选 2 个，作为该风格下的桥段偏好")
   ```
2. `data/elements/MX.elements.json` 补该类型数据（ID 形如 `MX-TRP-001`）
3. 若要参与组装，在 `compiler._build_card` 里把它塞进某个槽位

前端按注册表动态渲染，自动出现新的一栏；`as_public_list()` 负责这件事。

### 8.2 加一个新的剧本风格

只需新增 `data/styles/<id>.json`，抄 `mx-telenovela.json` 改四个块：

| 块 | 改什么 |
|---|---|
| `segments` | 段位名 + 比例 + 每段任务（决定全剧结构） |
| `timeline` | 五段的时长与"要求"文字 |
| `style_rules` / `system_prompt` / `episode_prompt` | 写法口味 |
| `degrade_templates` | 降级稿模板 + 语域映射 |

代码零改动。`mx-suspenso.json` 就是照这个做的（身份悬疑，段落比例 15/30/25/18/12）。

### 8.3 加一个新市场

1. 素材库按 `material-library/<mkt>/` 建齐（L0 红线 + L1 场景物品 + L2 叙事要素）
2. `tools/build_elements.py --market ID` → 产出 `data/elements/ID.elements.json`
3. `data/names/ID.json` 补姓名池
4. `data/styles/` 补该国风格包（L2 叙事要素大多可跨市场复用，L0/L1 必须重写）

**注意原库的铁律**：东南亚不能当一个市场，必须一国一库；`S2` 是"内核必须重写"而不是"换个地名"。

### 8.4 加一种导出

`core/exporter.py` 加一个 `to_xxx(script)` 返回字符串，在 `export_all` 里注册一行。

### 8.5 换成别的模型

设三个环境变量即可，代码不动：

```bash
SG_LLM_BASE_URL=https://api.deepseek.com/v1
SG_LLM_API_KEY=sk-...
SG_LLM_MODEL=deepseek-chat
```

---

## 9. 已知边界与下一步

| # | 边界 | 影响 | 建议 |
|---|---|---|---|
| 1 | 只有 MX 一个市场 | 无法验证跨市场复用 | 第二市场建议选印尼（人口最大、审查逻辑最清晰） |
| 2 | 支线 `SUB` 未接入 | 30 集只有主线，缺情绪放大器 | `08-subplots.md` 已建 32 条，只需补 `episode→subplot` 挂载表 |
| 3 | 台词库未接入 | 西语台词靠模型发挥，本地味不足 | 把 `05-dialogue.md` 的爆点句 + `07-dialogue-scenes.md` 的场景绑定做成"台词采样"塞进 prompt |
| 4 | `never_do` 检测是启发式 | 可能误报/漏报 | 现在是 warn 不阻断；要做准得上二次 LLM 审核 |
| 5 | 合规靠关键词 | 挡不住语义级踩线 | 红线是硬伤，建议产出后人工过一遍（原库已经这么要求） |
| 6 | 单集单场景为主 | 高潮集表现力有限 | 已支持高潮集换景一次；要更强得扩展 `scene` 的"连续动作"字段 |
| 7 | 无续写/局部重写 | 改一集要重跑整集 | 可加 `patch_episode(ep, instruction)`，走同一套校验 |
| 8 | 无成本计量 | 不知道跑了多少钱 | LLM 层已记录 model/tokens 入口，接个 usage 字段即可 |

---

## 10. 文件清单

```
script-generator/
├── README.md                   快速开始
├── cli.py                      命令行：list/validate/plan/prompt/gen
├── server.py                   零依赖 Web 服务（4 个 API + 静态页）
├── core/
│   ├── registry.py             ★ 要素类型注册表（扩展的唯一开关）
│   ├── models.py               Element / Library / Blueprint（含 6 条关联校验）
│   ├── compiler.py             ★ Blueprint → 60 张组装卡（爽点梯度/场景排布）
│   ├── renderer.py             ★ 组装卡 → prompt → 单集（含模板降级）
│   ├── llm.py                  OpenAI 兼容客户端（重试/错误分类/JSON 抠取）
│   ├── validator.py            ★ 3 层校验（结构/引用/规则）
│   ├── pipeline.py             5 阶段编排 + 并发 + 降级阶梯
│   ├── exporter.py             md/html/json/csv/fountain
│   └── errors.py               错误码体系
├── tools/build_elements.py     素材库 Markdown/JSON → 机器可读要素包
├── data/
│   ├── elements/MX.elements.json   412 条（自动生成，勿手改）
│   ├── styles/mx-telenovela.json   风格包 ×2
│   ├── styles/mx-suspenso.json
│   └── names/MX.json               西语名池
├── examples/blueprint-bag1.json    示例蓝图（包 1 顶替新娘）
├── web/                        index.html / app.js / style.css
├── tests/test_pipeline.py      69 项端到端自测
└── out/                        导出成品
```
