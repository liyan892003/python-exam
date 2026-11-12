"""把学生的模块设计图（JSON）转换成 Python 代码骨架。

图的数据结构：
{
  "nodes": [{"id": "main", "kind": "main", "name": "main", "params": "",
             "returns": "", "desc": "主程序", "x": 360, "y": 20},
            {"id": "n1", "kind": "func", "name": "getInputs", "params": "",
             "returns": "proA, proB, n", "desc": "读取三个输入", "x": 80, "y": 300}],
  "edges": [{"id": "e1", "from": "main", "to": "n1", "label": ""}]
}

生成规则：
- 每个模块 → 一个 def，参数取 node.params，函数体留 pass / return None 待补全
- main() 中按模块在画布上的位置（从上到下、从左到右）依次调用
- 连线上的 label 作为调用时传入的实参；node.returns 作为接收返回值的变量
"""
import json
import keyword
import re

MAX_NODES = 20
MAX_EDGES = 40
MAX_RAW_BYTES = 60_000

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_ident(name: str) -> bool:
    return bool(_IDENT_RE.match(name)) and not keyword.iskeyword(name)


def _one_line(text, limit: int = 80) -> str:
    """把任意文本压成一行，去掉会破坏字符串/注释的字符。"""
    s = " ".join(str(text or "").split())
    s = s.replace('"""', "'''")
    return s[:limit]


def _pos(node) -> float:
    try:
        return float(node.get("y", 0))
    except (TypeError, ValueError):
        return 0.0


def _pos_x(node) -> float:
    try:
        return float(node.get("x", 0))
    except (TypeError, ValueError):
        return 0.0


def parse_diagram(raw: str) -> tuple[dict, list[str]]:
    """解析并做基础校验，返回 (data, warnings)。"""
    warnings: list[str] = []
    if not raw:
        return {"nodes": [], "edges": []}, warnings
    if len(raw.encode("utf-8", "ignore")) > MAX_RAW_BYTES:
        return {"nodes": [], "edges": []}, ["设计图数据过大，请减少模块数量"]
    try:
        data = json.loads(raw)
    except Exception:
        return {"nodes": [], "edges": []}, ["设计图数据无法解析，请重新保存"]
    if not isinstance(data, dict):
        return {"nodes": [], "edges": []}, ["设计图数据格式不正确"]
    nodes = data.get("nodes")
    edges = data.get("edges")
    if not isinstance(nodes, list):
        nodes = []
    if not isinstance(edges, list):
        edges = []
    if len(nodes) > MAX_NODES:
        warnings.append(f"模块数量超过 {MAX_NODES} 个，多余的已被忽略")
        nodes = nodes[:MAX_NODES]
    if len(edges) > MAX_EDGES:
        warnings.append(f"连线数量超过 {MAX_EDGES} 条，多余的已被忽略")
        edges = edges[:MAX_EDGES]
    clean_nodes = [n for n in nodes if isinstance(n, dict)]
    clean_edges = [e for e in edges if isinstance(e, dict)]
    return {"nodes": clean_nodes, "edges": clean_edges}, warnings


def build_skeleton(raw: str) -> tuple[str, list[str]]:
    """根据设计图生成代码骨架，返回 (code, warnings)。"""
    data, warnings = parse_diagram(raw)
    nodes = data["nodes"]

    # 1. 校验函数名，去重
    valid: list[dict] = []
    seen: set[str] = set()
    main_node = None
    for n in nodes:
        name = str(n.get("name", "")).strip()
        if not _is_ident(name):
            warnings.append(f"模块「{name or '未命名'}」不是合法函数名，已跳过")
            continue
        if name in seen:
            warnings.append(f"函数名「{name}」重复，后面的同名模块已跳过")
            continue
        seen.add(name)
        if name == "main" or n.get("kind") == "main":
            if main_node is None:
                main_node = n
            else:
                warnings.append("只能有一个 main() 模块，多余的已忽略")
            continue
        valid.append(n)

    children = sorted(valid, key=lambda n: (_pos(n), _pos_x(n)))
    by_name = {str(n.get("name")).strip(): n for n in children}
    # 画布上的连线存的是节点 id，这里统一映射回函数名（包含 main）
    id_to_name = {}
    for n in ([main_node] if main_node else []) + children:
        id_to_name[str(n.get("id", ""))] = str(n.get("name")).strip()
    order = {name: i for i, name in enumerate(by_name)}
    order["main"] = -1

    def _resolve(raw_id) -> str:
        key = str(raw_id).strip()
        return id_to_name.get(key, key)

    def _calls_from(source_name: str) -> list[str]:
        """按画布上从这个模块出发的连线，生成函数体里的调用语句。"""
        edges = [e for e in data["edges"] if _resolve(e.get("from")) == source_name]
        edges.sort(key=lambda e: order.get(_resolve(e.get("to")), 10_000))
        lines: list[str] = []
        used: set[str] = set()
        for e in edges:
            target_name = _resolve(e.get("to"))
            target = by_name.get(target_name)
            if not target:
                if target_name and target_name != "main":
                    warnings.append(f"有一条连线指向不存在的模块「{target_name}」，已忽略")
                continue
            if target_name in used:
                continue
            used.add(target_name)
            # 线上没写参数时，自动用目标函数的参数
            args = " ".join(str(e.get("label", "")).split()) or " ".join(
                str(target.get("params", "")).split())
            call = f"{target_name}({args})"
            returns = str(target.get("returns", "")).strip()
            if returns:
                call = f"{returns} = {call}"
            desc = _one_line(target.get("desc"), 40)
            if desc:
                call = f"{call}  # {desc}"
            lines.append("    " + call)
        return lines

    # 2. 每个模块函数：函数体里的调用来自它自己的出边
    parts: list[str] = []
    for n in children:
        name = str(n.get("name")).strip()
        params = " ".join(str(n.get("params", "")).split())
        returns = str(n.get("returns", "")).strip()
        desc = _one_line(n.get("desc"))
        calls = _calls_from(name)
        body: list[str] = []
        if desc:
            body.append(f'    """{desc}"""')
        body.append(f"    # TODO: 返回 {returns}" if returns else f"    # TODO: 补全 {name} 的功能")
        body.extend(calls)
        if not calls:
            body.append("    pass")
        parts.append(f"def {name}({params}):\n" + "\n".join(body))

    # 3. main()：同样按 main 的出边生成调用（没连到 main 的函数也照常定义）
    if main_node is not None:
        main_calls = _calls_from("main")
        body = ['    """主程序：按设计图顺序调用各功能模块。"""'] + main_calls
        if not main_calls:
            body.append("    pass  # TODO: 在画布上连线，或自己写调用")
        parts.append("def main():\n" + "\n".join(body))
    else:
        warnings.append("还没有 main() 模块，无法生成主程序入口")

    header = ('"""由模块设计图自动生成的代码骨架 —— 请补全每个函数的实现。"""')
    code = "\n\n\n".join([header] + parts)
    if main_node is not None:
        code += '\n\n\nif __name__ == "__main__":\n    main()\n'
    else:
        code += "\n"
    return code, warnings


def diagram_summary(raw: str) -> dict:
    """给教师端/学生端做进度展示用的轻量统计。"""
    data, _ = parse_diagram(raw)
    nodes = [n for n in data["nodes"] if str(n.get("name", "")).strip()]
    return {"n_nodes": len(nodes), "n_edges": len(data["edges"])}
