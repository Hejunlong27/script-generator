# -*- coding: utf-8 -*-
"""要素类型注册表。

**这是「便于扩展新要素类型」的唯一开关。**
加一个新类型（比如 `TROPE 桥段`）只需要：
  1. 在 ELEMENT_TYPES 里加一条 TypeSpec
  2. 在 data/elements/<MARKET>.elements.json 里补该类型的数据
  3. 若需参与剧本组装，在 compiler 的 slot 计划里引用它的 type key
不需要改 validator / exporter / 前端（前端按注册表动态渲染）。

**step 决定用户看到的顺序。** 顺序不是审美问题，是理解成本问题：
用户脑内的构建顺序是「做什么类型 → 讲什么故事 → 卡在哪 → 怎么收 → 谁来做 → 在哪发生 → 第一眼看到什么」。
顺序错了，用户会在还不知道讲什么的时候被要求选场景，只能瞎点。

ID 规则：{MARKET}-{TYPE_CODE}-{LOCAL}
  LOCAL 兼容两种写法：三位数字（001，来自主库 JSON）或 字母+两位（C01，来自 picker）
  例：MX-SCN-001 / MX-ARC-C01  都能通过
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TypeSpec:
    key: str                      # 内部 key，蓝图与代码里用这个
    code: str                     # ID 中的类型码
    cn: str                       # 中文名
    layer: str                    # L0 / L1 / L2
    role: str = "optional"        # single=蓝图必选一项 / multi=多项 / pool=备查不选 / implicit=自动
    pick: int = 0                 # multi 类型建议选几项
    min_pick: int = 0
    hint: str = ""                # 给用户/前端的选法提示
    stage: str = ""               # 阶段名（前端分组标题）
    step: int = 99                # 呈现顺序，小的在前
    picker_module: str = ""       # 对应素材库文件序号，便于回溯
    text_field: str = "name"      # 生成 prompt 时取哪个字段作为主文本
    tags: tuple = field(default=())
    bound_type: str = ""          # 蓝图专用槽位绑定的要素类型（如 open_hook -> hook）


# 阶段：名字本身就是一句"这一步在定什么"
STAGE_GENRE = "1 题材 · 决定一切默认值"
STAGE_STORY = "2 剧情 · 讲什么、怎么走"
STAGE_TENSION = "3 张力 · 卡在哪"
STAGE_ENDING = "4 收束 · 怎么结束"
STAGE_CAST = "5 人物 · 谁来做"
STAGE_WORLD = "6 世界 · 在哪发生"
STAGE_HOOK = "7 钩子 · 第一眼看到什么"

ELEMENT_TYPES: dict[str, TypeSpec] = {
    # ---- L0 ----
    "market": TypeSpec(
        "market", "MKT", "市场底座", "L0", role="implicit", step=99,
        stage="", hint="不用选，按 meta.market 自动载入；红线与语言口径来自这里",
        text_field="name",
    ),
    # ================= 1 题材 =================
    "package": TypeSpec(
        "package", "PKG", "题材组合包", "L2", role="single", pick=1, min_pick=1,
        stage=STAGE_GENRE, step=10, text_field="name",
        hint="先定这个。选完会自动带出剧情内核与主线骨架的推荐，后面的候选项也会按它收窄。",
    ),
    "genre": TypeSpec(
        "genre", "GEN", "平台题材标签", "L2", role="pool", stage=STAGE_GENRE, step=11,
        text_field="attrs.label",
        hint="45 个投放标签，A 直接用 / B 换皮 / C 重写内核 / D 别碰。不单独选，用于标注组合包的风险。",
    ),
    # ================= 2 剧情 =================
    "core": TypeSpec(
        "core", "COR", "剧情内核", "L2", role="single", pick=1, min_pick=1,
        stage=STAGE_STORY, step=20, picker_module="02", text_field="attrs.one_line",
        hint="一句话说清这部剧在讲什么欲望。这是卖点和投放文案的来源，不是结构。",
    ),
    "arc": TypeSpec(
        "arc", "ARC", "主线骨架", "L2", role="single", pick=1, min_pick=1,
        stage=STAGE_STORY, step=30, picker_module="03", text_field="attrs.skeleton",
        hint="这条欲望怎么走完：起点 → 中点反转 → 终点。选完内核后，这里会标出作者配对项。",
    ),
    # ================= 3 张力 =================
    "conflict": TypeSpec(
        "conflict", "CFT", "冲突内核", "L2", role="multi", pick=3, min_pick=3,
        stage=STAGE_TENSION, step=40, picker_module="06", text_field="attrs.conflict",
        hint="必须 3 个，且凑齐 外部 / 内部 / 结构 三层。三个全是外部，全剧必塌。",
    ),
    # ================= 4 收束 =================
    "ending": TypeSpec(
        "ending", "END", "结局走向", "L2", role="single", pick=1, min_pick=0,
        stage=STAGE_ENDING, step=50, picker_module="08", text_field="attrs.ending",
        hint="怎么收 + 付出什么代价。没有代价的结局没有重量。不选则按内核兜底。",
    ),
    # ================= 5 人物 =================
    "character": TypeSpec(
        "character", "CHR", "主角人设", "L2", role="single", pick=1, min_pick=1,
        stage=STAGE_CAST, step=60, picker_module="04", text_field="attrs.identity",
        hint="身份｜表面｜缺陷｜秘密。缺陷必须可演。必须与主线骨架至少共享 1 个标签。",
    ),
    "support": TypeSpec(
        "support", "SPT", "对手与配角", "L2", role="multi", pick=2, min_pick=1,
        stage=STAGE_CAST, step=70, picker_module="05", text_field="attrs.identity",
        hint="选 2 个：1 个对手（E01–E15）+ 1 个功能配角。每个人都要有自己的「不可做」。",
    ),
    # ================= 6 世界 =================
    "city": TypeSpec(
        "city", "LOC", "城市背景", "L1", role="single", pick=1, min_pick=1,
        stage=STAGE_WORLD, step=80, picker_module="01", text_field="attrs.visual",
        hint="视觉基调与阶级默认值。选 1 个，选完回头看人物是否还成立。",
    ),
    "scene": TypeSpec(
        "scene", "SCN", "场景", "L1", role="pool", stage=STAGE_WORLD, step=81,
        text_field="name",
        hint="不手选：由编译器按成本与可拍人数自动排布（30 集用满 18–22 个）",
    ),
    "prop": TypeSpec(
        "prop", "OBJ", "物品", "L1", role="pool", stage=STAGE_WORLD, step=82,
        text_field="name",
        hint="不手选：按场景自带道具与角色视觉符号自动配对",
    ),
    # ================= 7 钩子 =================
    "hook": TypeSpec(
        "hook", "HOK", "钩子", "L2", role="pool", stage=STAGE_HOOK, step=91,
        text_field="attrs.visual",
        hint="开场钩由蓝图指定 1 个，集尾钩由编译器按段位自动配",
    ),
    "beat": TypeSpec(
        "beat", "BEAT", "爽点", "L2", role="pool", stage=STAGE_HOOK, step=92,
        text_field="attrs.text",
        hint="不手选：按梯度铁律（相邻差≤2）自动排布",
    ),
    # ---- 预留：加新要素类型就在这里加 ----
    # "trope": TypeSpec("trope", "TRP", "桥段", "L2", role="multi", pick=2,
    #                   stage="2 剧情 · 讲什么、怎么走", step=35, text_field="attrs.text"),
}


# 蓝图专用槽位：不新增要素类型，只是把已有类型钉到某个固定位置
BLUEPRINT_ONLY: dict[str, TypeSpec] = {
    "open_hook": TypeSpec(
        "open_hook", "HOK", "开场钩子", "L2", role="single", pick=1, min_pick=0,
        stage=STAGE_HOOK, step=90, text_field="attrs.visual", bound_type="hook",
        hint="3 秒画面，决定前 3 秒留存。最后选——这时你已经知道全剧在讲什么了。",
    ),
}


# 蓝图槽位别名：写 hero 或 character 都行（hero 更好读，character 是要素类型 key）
SLOT_ALIASES = {"hero": "character", "supports": "support", "conflicts": "conflict",
                "cities": "city", "arcs": "arc", "endings": "ending",
                "packages": "package", "genres": "genre"}


def canon_slot(key: str) -> str:
    return SLOT_ALIASES.get(key, key)


def get_type(key: str) -> TypeSpec:
    key = canon_slot(key)
    try:
        return ELEMENT_TYPES[key]
    except KeyError:
        raise KeyError(f"未注册的要素类型: {key!r}；可用: {', '.join(ELEMENT_TYPES)}") from None


def type_by_code(code: str) -> TypeSpec | None:
    for spec in ELEMENT_TYPES.values():
        if spec.code == code:
            return spec
    return None


def blueprint_slots() -> list[tuple[str, TypeSpec]]:
    """蓝图需要用户显式选择的所有槽位，按 step 排序（implicit / pool 不算）"""
    out = [(k, s) for k, s in ELEMENT_TYPES.items() if s.role in ("single", "multi")]
    out += list(BLUEPRINT_ONLY.items())
    out.sort(key=lambda kv: (kv[1].step, kv[0]))
    return out


def slot_element_type(slot: str, spec: TypeSpec | None = None) -> str:
    """槽位期望的要素类型（用于 type mismatch 校验）"""
    sp = spec or ELEMENT_TYPES.get(canon_slot(slot)) or BLUEPRINT_ONLY.get(slot)
    if sp is None:
        return canon_slot(slot)
    return sp.bound_type or canon_slot(slot)


def as_public_list() -> list[dict]:
    """给前端渲染选择器用（按 step 排序，前端不再自己排）"""
    out = []
    for k, s in blueprint_slots():
        if s.role in ("single", "multi"):
            out.append(
                {
                    "key": k, "code": s.code, "cn": s.cn, "layer": s.layer,
                    "role": s.role, "pick": s.pick, "min_pick": s.min_pick,
                    "hint": s.hint, "stage": s.stage, "step": s.step,
                    "text_field": s.text_field, "bound_type": s.bound_type,
                }
            )
    return out
