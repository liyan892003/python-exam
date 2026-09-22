# Python 徽章体系规格 · Badge System Spec

> 面向实现的规格文件。前半为说明,末尾 `## 完整目录 (JSON)` 为权威数据源,可直接被代码/AI 解析。
> 版本 v1.0 · 所有数字为默认值,集中在 `config` 与 `tiers` 中,可调。

---

## 1. 概述

徽章分三大家族,外加一组单枚知识点徽章:

| 家族 | 含义 | 解锁方式 |
|---|---|---|
| **A 知识点进阶线** | 每个核心知识点的纵向精通,4 级(青铜→白银→紫金→黄金) | 累计答对题数 + 综合任务 |
| **B 计算思维徽章** | 横向的计算/编程思维能力,12 枚,已分布于四级 | 思维专项任务 或 思维判断题 |
| **C 成就 & 趣味徽章** | 里程碑与彩蛋 | 特定事件触发,达成即得 |
| **D 单枚知识点** | 较小、无需分级的知识点 | 固定题数一次性解锁 |

> 说明:**计算思维**在体系中出现两次——作为独立进阶线(A 家族 `computational_thinking`,纵向逐级),以及作为里程碑徽章(B 家族的分解/模式识别/抽象/算法设计四枚)。二者可并存,也可按需二选一实现。

---

## 2. 等级与解锁规则

### 2.1 四个等级(进阶线通用)

每个**知识点独立累计**。题数为累计值(达到高级自动包含低级)。

| 等级 | 累计答对 | 进阶题(★★)下限 | 挑战题(★★★)下限 | 正确率 | 附加条件 |
|---|---|---|---|---|---|
| 🟤 青铜 Bronze | 10 | 0 | 0 | ≥ 60% | — |
| ⚪ 白银 Silver | 25 | 8 | 0 | ≥ 65% | — |
| 🟣 紫金 Epic | 45 | 18 | 0 | ≥ 75% | — |
| 🟡 黄金 Legendary | 70 | — | 10 | ≥ 80% | 完成 1 个该主题综合任务 |

### 2.2 题目难度分档

| 档位 | 标记 | 说明 |
|---|---|---|
| 基础 | ★ | 概念理解、语法直接应用 |
| 进阶 | ★★ | 多步骤、组合运用 |
| 挑战 | ★★★ | 综合、开放、贴近真实场景(黄金级综合任务由 rubric 评定) |

### 2.3 其他家族解锁规则

- **B 计算思维徽章:** 每枚 = 完成 **2** 个该思维专项任务 **或** 答对 **8** 道该思维判断题(任一即可)。
- **D 单枚知识点:** 答对 **15** 道该点题目(基础+进阶),正确率 ≥ **70%**。
- **C 成就徽章:** 由 `trigger` 字段描述的事件触发,达成即得。

### 2.4 计数与防刷

- 同一题只在**首次答对**时计入。
- 正确率低于门槛不解锁。
- 每日计入上限 **30** 题(可调)。

---

## 3. 展示规则

- **只显示已获得的徽章;不渲染未解锁的灰色占位徽章。**
- 可选:用**一行文字**提示下一目标(如"距离白银还差 6 题"),但不显示灰色徽章本身。
- 段位积分(可选):青铜 1 / 白银 3 / 紫金 6 / 黄金 10,用于排行与学期结算。
- 集齐一整条进阶线可授予特别称号(如"循环通关")。

---

## 4. 解锁判定逻辑(伪代码)

```python
def tier_for_track(stats, tiers):
    # stats.correct = {"basic": int, "advanced": int, "challenge": int}
    # stats.accuracy = float(0~1); stats.tasks_done = int
    total = stats.correct["basic"] + stats.correct["advanced"] + stats.correct["challenge"]
    for tier in reversed(tiers):            # 从最高级往下检查
        if (total >= tier["cumulative_correct"]
                and stats.correct["advanced"] >= tier["min_advanced"]
                and stats.correct["challenge"] >= tier["min_challenge"]
                and stats.accuracy >= tier["min_accuracy"]
                and (not tier["requires_task"] or stats.tasks_done >= 1)):
            return tier["id"]
    return None                             # 未达青铜:不显示徽章

def thinking_unlocked(stats, cfg):          # B 家族
    return stats.tasks_done >= cfg["thinking_tasks"] or stats.questions_correct >= cfg["thinking_questions"]

def single_unlocked(stats, cfg):            # D 家族
    return stats.questions_correct >= cfg["single_questions"] and stats.accuracy >= cfg["single_accuracy"]
```

---

## 5. 完整目录 (JSON)

> 权威数据源。`families.A_tracks[].levels[]` 的每一级用 2.1 的 `tiers` 门槛解锁。

```json
{
  "version": "1.0",
  "config": {
    "thinking_tasks": 2,
    "thinking_questions": 8,
    "single_questions": 15,
    "single_accuracy": 0.70,
    "daily_count_cap": 30,
    "count_first_correct_only": true,
    "tier_scores": { "bronze": 1, "silver": 3, "epic": 6, "legend": 10 }
  },
  "display": {
    "show_only_earned": true,
    "render_locked_placeholder": false,
    "show_next_tier_progress_hint": true
  },
  "difficulty": [
    { "id": "basic",     "star": 1, "name_zh": "基础" },
    { "id": "advanced",  "star": 2, "name_zh": "进阶" },
    { "id": "challenge", "star": 3, "name_zh": "挑战" }
  ],
  "tiers": [
    { "id": "bronze", "order": 1, "name_zh": "青铜", "name_en": "Bronze",     "color": "#e0a46a", "cumulative_correct": 10, "min_advanced": 0,  "min_challenge": 0,  "min_accuracy": 0.60, "requires_task": false, "score": 1 },
    { "id": "silver", "order": 2, "name_zh": "白银", "name_en": "Silver",     "color": "#a9d2e8", "cumulative_correct": 25, "min_advanced": 8,  "min_challenge": 0,  "min_accuracy": 0.65, "requires_task": false, "score": 3 },
    { "id": "epic",   "order": 3, "name_zh": "紫金", "name_en": "Epic",       "color": "#b99df2", "cumulative_correct": 45, "min_advanced": 18, "min_challenge": 0,  "min_accuracy": 0.75, "requires_task": false, "score": 6 },
    { "id": "legend", "order": 4, "name_zh": "黄金", "name_en": "Legendary",  "color": "#f5d678", "cumulative_correct": 70, "min_advanced": 0,  "min_challenge": 10, "min_accuracy": 0.80, "requires_task": true,  "score": 10 }
  ],
  "families": {
    "A_tracks": [
      { "id": "loop", "name_zh": "循环", "focus": "让代码替你重复,直到懂得何时不该循环", "levels": [
        { "tier": "bronze", "id": "loop_1", "name_en": "First Loop",     "name_zh": "初次循环试探", "knowledge": "for 循环基础、range" },
        { "tier": "silver", "id": "loop_2", "name_en": "Loop Handler",   "name_zh": "循环好手",     "knowledge": "while、break/continue、嵌套循环" },
        { "tier": "epic",   "id": "loop_3", "name_en": "Loop Master",    "name_zh": "循环大师",     "knowledge": "enumerate/zip、列表推导式" },
        { "tier": "legend", "id": "loop_4", "name_en": "Loop Warrior",   "name_zh": "循环战士",     "knowledge": "向量化/生成器、何时避免显式循环" }
      ]},
      { "id": "function", "name_zh": "函数", "focus": "把逻辑装进盒子,直到函数成为一等公民", "levels": [
        { "tier": "bronze", "id": "function_1", "name_en": "First Function",    "name_zh": "初写函数",   "knowledge": "def、参数、return" },
        { "tier": "silver", "id": "function_2", "name_en": "Function Crafter",  "name_zh": "函数熟手",   "knowledge": "默认参数、*args/**kwargs、多返回值" },
        { "tier": "epic",   "id": "function_3", "name_en": "Function Master",   "name_zh": "函数大师",   "knowledge": "高阶函数、lambda、map/filter、作用域与闭包" },
        { "tier": "legend", "id": "function_4", "name_en": "Function Sage",     "name_zh": "函数宗师",   "knowledge": "装饰器、函数式思维" }
      ]},
      { "id": "data_structure", "name_zh": "数据结构", "focus": "给数据安个家,直到按复杂度选对容器", "levels": [
        { "tier": "bronze", "id": "data_structure_1", "name_en": "First List",         "name_zh": "初识列表",   "knowledge": "list 增删改查、索引切片" },
        { "tier": "silver", "id": "data_structure_2", "name_en": "Structure Handler",  "name_zh": "结构好手",   "knowledge": "dict、set、tuple、解包" },
        { "tier": "epic",   "id": "data_structure_3", "name_en": "Structure Master",   "name_zh": "结构大师",   "knowledge": "嵌套结构、推导式" },
        { "tier": "legend", "id": "data_structure_4", "name_en": "Structure Sage",     "name_zh": "结构宗师",   "knowledge": "复杂度直觉与结构选型" }
      ]},
      { "id": "string", "name_zh": "字符串", "focus": "从拼接到正则,驾驭文本", "levels": [
        { "tier": "bronze", "id": "string_1", "name_en": "First Words",   "name_zh": "初识字符串", "knowledge": "拼接、索引、f-string" },
        { "tier": "silver", "id": "string_2", "name_en": "Text Handler",  "name_zh": "文本好手",   "knowledge": "split/join/replace/查找/大小写" },
        { "tier": "epic",   "id": "string_3", "name_en": "Text Master",   "name_zh": "文本大师",   "knowledge": "切片技巧、格式化进阶、编码" },
        { "tier": "legend", "id": "string_4", "name_en": "Regex Ranger",  "name_zh": "正则游侠",   "knowledge": "正则表达式匹配与提取" }
      ]},
      { "id": "oop", "name_zh": "面向对象", "focus": "从建类到面向对象设计", "levels": [
        { "tier": "bronze", "id": "oop_1", "name_en": "First Class",         "name_zh": "初建类",   "knowledge": "class/__init__/self、实例化" },
        { "tier": "silver", "id": "oop_2", "name_en": "Object Handler",      "name_zh": "对象好手", "knowledge": "封装、property、实例vs类属性" },
        { "tier": "epic",   "id": "oop_3", "name_en": "Inheritance Master",  "name_zh": "继承大师", "knowledge": "继承、多态、魔术方法" },
        { "tier": "legend", "id": "oop_4", "name_en": "Design Sage",         "name_zh": "设计宗师", "knowledge": "dataclass、组合优于继承、OOP 设计" }
      ]},
      { "id": "debug", "name_zh": "调试", "focus": "与 bug 的持久战:从 print 试探到系统化围剿", "levels": [
        { "tier": "bronze", "id": "debug_1", "name_en": "First Catch",  "name_zh": "初次捉虫", "knowledge": "print 调试、定位首个 bug" },
        { "tier": "silver", "id": "debug_2", "name_en": "Bug Reader",   "name_zh": "读错高手", "knowledge": "读懂 Traceback" },
        { "tier": "epic",   "id": "debug_3", "name_en": "Bug Hunter",   "name_zh": "猎虫能手", "knowledge": "断点调试、assert 断言" },
        { "tier": "legend", "id": "debug_4", "name_en": "Bug Slayer",   "name_zh": "除虫大师", "knowledge": "二分排查、最小可复现、logging" }
      ]},
      { "id": "pandas", "name_zh": "数据分析(pandas)", "focus": "财经方向:从读数到洞察", "levels": [
        { "tier": "bronze", "id": "pandas_1", "name_en": "First Frame",     "name_zh": "初见数据", "knowledge": "读 CSV/Excel、查看 DataFrame" },
        { "tier": "silver", "id": "pandas_2", "name_en": "Data Cleaner",    "name_zh": "清洗好手", "knowledge": "缺失值、筛选、排序、类型转换" },
        { "tier": "epic",   "id": "pandas_3", "name_en": "Analysis Master", "name_zh": "分析大师", "knowledge": "groupby、透视表、merge/join" },
        { "tier": "legend", "id": "pandas_4", "name_en": "Insight Sage",    "name_zh": "洞察宗师", "knowledge": "时间序列、可视化、完整分析报告" }
      ]},
      { "id": "computational_thinking", "name_zh": "计算思维", "focus": "计算思维四大支柱的纵向进阶", "levels": [
        { "tier": "bronze", "id": "ct_1", "name_en": "Decomposer",          "name_zh": "化整为零", "knowledge": "分解:把问题拆成清晰的小步骤" },
        { "tier": "silver", "id": "ct_2", "name_en": "Pattern Finder",      "name_zh": "见微知著", "knowledge": "模式识别:发现重复与规律" },
        { "tier": "epic",   "id": "ct_3", "name_en": "Abstractor",          "name_zh": "去芜存菁", "knowledge": "抽象:抓本质、隐藏细节" },
        { "tier": "legend", "id": "ct_4", "name_en": "Algorithm Architect", "name_zh": "运筹帷幄", "knowledge": "算法设计:设计解法、比较方案、评估复杂度" }
      ]}
    ],
    "B_thinking": [
      { "id": "rubber_duck",           "tier": "bronze", "name_en": "Rubber Duck Sensei",   "name_zh": "小黄鸭大师",     "mindset": "小黄鸭调试法(讲解式排错)" },
      { "id": "test_believer",         "tier": "bronze", "name_en": "Test Believer",        "name_zh": "测试信徒",       "mindset": "测试思维(写完必验)" },
      { "id": "dry_disciple",          "tier": "bronze", "name_en": "DRY Disciple",         "name_zh": "绝不重复",       "mindset": "复用 / DRY" },
      { "id": "edge_hunter",           "tier": "silver", "name_en": "Edge Hunter",          "name_zh": "边界猎人",       "mindset": "边界思维(空值/极端输入)" },
      { "id": "iteration_sage",        "tier": "silver", "name_en": "Iteration Sage",       "name_zh": "小步快跑",       "mindset": "迭代思维(先跑通再优化)" },
      { "id": "code_archaeologist",    "tier": "silver", "name_en": "Code Archaeologist",   "name_zh": "代码考古学家",   "mindset": "读代码(读懂他人/旧代码)" },
      { "id": "divide_conquer",        "tier": "epic",   "name_en": "Divide & Conquer",     "name_zh": "分而治之",       "mindset": "分解(计算思维支柱)" },
      { "id": "pattern_seer",          "tier": "epic",   "name_en": "Pattern Seer",         "name_zh": "模式先知",       "mindset": "模式识别(计算思维支柱)" },
      { "id": "automation_alchemist",  "tier": "epic",   "name_en": "Automation Alchemist", "name_zh": "偷懒有道",       "mindset": "自动化思维" },
      { "id": "abstraction_architect", "tier": "legend", "name_en": "Abstraction Architect","name_zh": "抽象建筑师",     "mindset": "抽象(计算思维支柱)" },
      { "id": "blueprint_thinker",     "tier": "legend", "name_en": "Blueprint Thinker",    "name_zh": "谋定后动",       "mindset": "算法设计(计算思维支柱)" },
      { "id": "model_maker",           "tier": "legend", "name_en": "Model Maker",          "name_zh": "建模师",         "mindset": "建模思维(现实→数据结构)" }
    ],
    "C_achievements": [
      { "id": "first_light",      "name_en": "First Light",      "name_zh": "初次点亮", "trigger": "成功运行人生第一个 Python 程序" },
      { "id": "bug_whisperer",    "name_en": "Bug Whisperer",    "name_zh": "除虫者",   "trigger": "独立定位并修复第一个 bug" },
      { "id": "pythonic_master",  "name_en": "Pythonic Master",  "name_zh": "优雅之道", "trigger": "把一段代码重构为地道 Pythonic 风格并获认可" },
      { "id": "zen_seeker",       "name_en": "Zen Seeker",       "name_zh": "禅意寻者", "trigger": "运行 import this 并提交一段感悟" },
      { "id": "flawless_streak",  "name_en": "Flawless Streak",  "name_zh": "全勤满分", "trigger": "某单元练习连续满分 / 达成全勤" },
      { "id": "quant_rookie",     "name_en": "Quant Rookie",     "name_zh": "量化新锐", "trigger": "完成一个金融数据分析小项目" },
      { "id": "one_liner_legend", "name_en": "One-Liner Legend", "name_zh": "一行传奇", "trigger": "用一行可读的代码完成他人十行的任务" }
    ],
    "D_single_points": [
      { "id": "conditionals",     "name_zh": "条件判断",              "focus": "if/elif/else、三元表达式" },
      { "id": "truthiness",       "name_zh": "真值与逻辑",            "focus": "truthy/falsy、短路求值" },
      { "id": "modules",          "name_zh": "模块与标准库",          "focus": "import、math/random/datetime/collections/json" },
      { "id": "file_io",          "name_zh": "文件与 I/O",           "focus": "with 上下文、编码、CSV/JSON、pathlib" },
      { "id": "exceptions",       "name_zh": "异常处理",              "focus": "try/except/finally、raise、自定义异常" },
      { "id": "environment",      "name_zh": "环境与生态",            "focus": "pip、虚拟环境、__main__" },
      { "id": "advanced_features","name_zh": "进阶特性",              "focus": "生成器/yield、上下文管理器、typing" }
    ]
  }
}
```

---

## 6. 徽章数量小结

- A 进阶线:8 线 × 4 级 = **32**
- B 计算思维:**12**
- C 成就 & 趣味:**7**(可扩展)
- D 单枚知识点:**7**
- **合计约 58 枚**(数字与家族均可按课程增删)
