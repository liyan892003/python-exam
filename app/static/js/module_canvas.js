/* 模块化设计画布：拖拽函数模块、连线并标注传递的参数。
 *
 * 用法（模板）：
 *   <div data-module-canvas data-diagram='{...}' data-save-url="/student/projects/1/diagram">
 *     <div data-toolbar>...</div>
 *     <div data-canvas><svg data-svg></svg><div data-nodes></div></div>
 *     <div data-inspector></div>
 *   </div>
 * 只读模式：data-readonly="1"（教师查看学生作品）
 */
(function () {
  "use strict";

  const NODE_W = 190;
  const SVG_NS = "http://www.w3.org/2000/svg";

  function uid(prefix) {
    return prefix + Math.random().toString(36).slice(2, 8);
  }

  function svgEl(tag, attrs) {
    const el = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach((k) => el.setAttribute(k, attrs[k]));
    return el;
  }

  function defaultMain() {
    return {
      id: "main", kind: "main", name: "main",
      params: "", returns: "", desc: "主程序：依次调用下面的功能模块",
      x: 0, y: 0
    };
  }

  class ModuleDesigner {
    constructor(root) {
      this.root = root;
      this.readonly = root.dataset.readonly === "1";
      this.saveUrl = root.dataset.saveUrl || "";
      this.canvas = root.querySelector("[data-canvas]");
      this.svg = root.querySelector("[data-svg]");
      this.layer = root.querySelector("[data-nodes]");
      this.inspector = root.querySelector("[data-inspector]");
      this.statusEl = root.querySelector("[data-status]");
      this.linkLabelInput = root.querySelector("[data-link-label]");
      this.linkBtn = root.querySelector('[data-act="link"]');
      this.fromSel = root.querySelector("[data-from-select]");
      this.toSel = root.querySelector("[data-to-select]");
      this.paramInput = root.querySelector("[data-param-input]");

      this.nodes = [];
      this.edges = [];
      this.selectedId = null;
      this.linkMode = false;
      this.linkFrom = null;
      this.drag = null;
      this.saveTimer = null;
      this.saving = false;

      this.load();
      this.bind();
      this.renderAll();
      this.saveNow = this.saveNow.bind(this);
      this.root.designer = this;
    }

    /* ---------- 数据 ---------- */
    load() {
      let data = {};
      try {
        data = JSON.parse(this.root.dataset.diagram || "{}") || {};
      } catch (e) {
        data = {};
      }
      const rawNodes = Array.isArray(data.nodes) ? data.nodes : [];
      const rawEdges = Array.isArray(data.edges) ? data.edges : [];

      this.nodes = rawNodes.map((n) => ({
        id: String(n.id || uid("n")),
        kind: n.kind === "main" ? "main" : "func",
        name: String(n.name || "func"),
        params: String(n.params || ""),
        returns: String(n.returns || ""),
        desc: String(n.desc || ""),
        x: Number(n.x) || 0,
        y: Number(n.y) || 0
      }));
      if (!this.nodes.some((n) => n.kind === "main")) {
        this.nodes.unshift(defaultMain());
      }
      this.edges = rawEdges
        .filter((e) => e && e.from && e.to && e.from !== e.to)
        .map((e) => ({
          id: String(e.id || uid("e")),
          from: String(e.from),
          to: String(e.to),
          label: String(e.label || "")
        }));

      this.layoutIfNeeded();
    }

    layoutIfNeeded() {
      const w = this.canvasWidth();
      const main = this.nodes.find((n) => n.kind === "main");
      const funcs = this.nodes.filter((n) => n.kind !== "main");
      const hasPositions = this.nodes.some((n) => n.x > 0 || n.y > 0);
      if (!hasPositions) {
        if (main) {
          main.x = Math.max(20, w / 2 - NODE_W / 2);
          main.y = 16;
        }
        funcs.forEach((n, i) => {
          n.x = 20 + (i % 3) * (NODE_W + 34);
          n.y = 300 + Math.floor(i / 3) * 130;
        });
      }
    }

    canvasWidth() {
      return Math.max(this.canvas ? this.canvas.clientWidth : 0, 320);
    }

    toJSON() {
      return {
        nodes: this.nodes.map((n) => ({
          id: n.id, kind: n.kind, name: n.name, params: n.params,
          returns: n.returns, desc: n.desc,
          x: Math.round(n.x), y: Math.round(n.y)
        })),
        edges: this.edges.map((e) => ({
          id: e.id, from: e.from, to: e.to, label: e.label
        }))
      };
    }

    nodeById(id) {
      return this.nodes.find((n) => n.id === id) || null;
    }

    /* ---------- 渲染 ---------- */
    renderAll() {
      this.renderNodes();
      this.renderEdges();
      this.renderInspector();
      this.refreshSelects();
      this.updateLinkButton();
    }

    refreshSelects() {
      if (!this.fromSel || !this.toSel) return;
      const options = this.nodes.map((n) => ({ id: n.id, label: n.name + "()" }));
      [this.fromSel, this.toSel].forEach((sel) => {
        const keep = sel.value;
        sel.innerHTML = "";
        options.forEach((o) => {
          const op = document.createElement("option");
          op.value = o.id;
          op.textContent = o.label;
          sel.appendChild(op);
        });
        if (options.some((o) => o.id === keep)) sel.value = keep;
      });
      if (this.fromSel.value === this.toSel.value) {
        const other = this.nodes.find((n) => n.id !== this.fromSel.value);
        if (other) this.toSel.value = other.id;
      }
    }

    renderNodes() {
      this.layer.innerHTML = "";
      this.nodes.forEach((n) => {
        const el = document.createElement("button");
        el.type = "button";
        el.className = "md-node";
        el.title = n.name + "()";
        el.dataset.id = n.id;
        el.style.cssText = [
          "position:absolute",
          "display:block",
          "text-align:left",
          "font-family:inherit",
          "width:" + NODE_W + "px",
          "left:" + n.x + "px",
          "top:" + n.y + "px",
          "border-radius:18px",
          "padding:10px 12px",
          "box-shadow:0 4px 10px rgba(109,40,217,.12)",
          "border:3px solid " + (n.kind === "main" ? "#14b8a6" : "#c4b5fd"),
          "background:" + (n.kind === "main" ? "#2dd4bf" : "#ffffff"),
          "color:" + (n.kind === "main" ? "#ffffff" : "#334155"),
          "cursor:" + (this.readonly ? "default" : "move"),
          "user-select:none",
          "z-index:3"
        ].join(";");
        if (this.selectedId === n.id) el.style.outline = "3px dashed #f472b6";
        if (this.linkFrom === n.id) el.style.outline = "3px solid #f59e0b";

        const title = document.createElement("div");
        title.style.cssText = "font-weight:800;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;";
        title.textContent = n.name + "(" + n.params + ")";
        el.appendChild(title);

        if (n.returns.trim()) {
          const r = document.createElement("div");
          r.style.cssText = "font-size:10px;margin-top:2px;opacity:.85;";
          r.textContent = "返回：" + n.returns;
          el.appendChild(r);
        }
        if (n.desc.trim()) {
          const d = document.createElement("div");
          d.style.cssText = "font-size:10px;margin-top:2px;opacity:.75;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;";
          d.textContent = n.desc;
          el.appendChild(d);
        }

        el.addEventListener("pointerdown", (e) => this.onNodePointerDown(e, n));
        el.addEventListener("click", (e) => this.onNodeClick(e, n));
        this.layer.appendChild(el);
        n._el = el;
      });
    }

    nodeRect(n) {
      const el = n._el;
      if (!el) return { x: n.x, y: n.y, w: NODE_W, h: 60 };
      return { x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight };
    }

    renderEdges() {
      const countEl = this.root.querySelector("[data-edge-count]");
      if (countEl) countEl.textContent = "连线 " + this.edges.length + " 条";
      this.svg.innerHTML = "";
      const defs = svgEl("defs");
      [["md-arrow-green", "#10b981"], ["md-arrow-red", "#ef4444"],
       ["md-arrow-purple", "#a78bfa"]].forEach(([id, color]) => {
        const marker = svgEl("marker", {
          id: id, viewBox: "0 0 10 10", refX: "8", refY: "5",
          markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse"
        });
        marker.appendChild(svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: color }));
        defs.appendChild(marker);
      });
      this.svg.appendChild(defs);

      this.edges.forEach((edge, edgeIndex) => {
        const source = this.nodeById(edge.from);
        const target = this.nodeById(edge.to);
        if (!source || !target) return;
        const ra = this.nodeRect(source);
        const rb = this.nodeRect(target);
        const ca = { x: ra.x + ra.w / 2, y: ra.y + ra.h / 2 };
        const cb = { x: rb.x + rb.w / 2, y: rb.y + rb.h / 2 };
        const p1 = this.anchor(ra, cb);
        const p2 = this.anchor(rb, ca);
        const isReturn = target.kind === "main";

        // 传入：绿色实线 caller → callee；返回：红色虚线 callee → caller，两条线错开不重合
        const outCurve = this.buildCurve(p1, p2, isReturn ? -9 : 9);
        this.drawCurve(outCurve, isReturn ? "#dc2626" : "#10b981",
                       isReturn ? "md-arrow-red" : "md-arrow-green", isReturn);
        this.addHit(outCurve.d, edge);

        const retDy = (edgeIndex % 2) * 26;   // 返回标签错开高度，避免相邻两条撞在一起
        if (isReturn) {
          const rv = source.returns.trim();
          if (rv) this.drawChip(outCurve, 0.55, "返回 " + rv, "#dc2626", "#fef2f2", retDy);
          return;
        }

        const args = edge.label.trim() || target.params.trim();
        if (args) this.drawChip(outCurve, 0.75, "传入 " + args, "#047857", "#ecfdf5");

        const rv = target.returns.trim();
        const hasBack = this.edges.some((x) => x.from === edge.to && x.to === edge.from);
        if (rv && !hasBack) {
          const backCurve = this.buildCurve(p2, p1, 9);
          this.drawCurve(backCurve, "#dc2626", "md-arrow-red", true);
          this.drawChip(backCurve, 0.55, "返回 " + rv, "#dc2626", "#fef2f2", retDy);
        }
      });
    }

    buildCurve(p1, p2, shift) {
      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len;
      const ny = dx / len;
      const a = { x: p1.x + nx * shift, y: p1.y + ny * shift };
      const b = { x: p2.x + nx * shift, y: p2.y + ny * shift };
      const vertical = Math.abs(b.y - a.y) >= Math.abs(b.x - a.x);
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      const c1 = vertical ? { x: a.x, y: my } : { x: mx, y: a.y };
      const c2 = vertical ? { x: b.x, y: my } : { x: mx, y: b.y };
      const d = "M " + a.x + " " + a.y + " C " + c1.x + " " + c1.y +
                ", " + c2.x + " " + c2.y + ", " + b.x + " " + b.y;
      return { a, b, c1, c2, d };
    }

    drawCurve(curve, color, marker, dashed) {
      const attrs = {
        d: curve.d, fill: "none", stroke: color, "stroke-width": "2.2",
        "marker-end": "url(#" + marker + ")"
      };
      if (dashed) attrs["stroke-dasharray"] = "6 5";
      this.svg.appendChild(svgEl("path", attrs));
    }

    drawChip(curve, t, text, color, fill, dy) {
      const mt = 1 - t;
      const x = mt * mt * mt * curve.a.x + 3 * mt * mt * t * curve.c1.x +
                3 * mt * t * t * curve.c2.x + t * t * t * curve.b.x;
      const y = mt * mt * mt * curve.a.y + 3 * mt * mt * t * curve.c1.y +
                3 * mt * t * t * curve.c2.y + t * t * t * curve.b.y + (dy || 0);
      const cjk = (text.match(/[\u4e00-\u9fa5]/g) || []).length;
      const w = Math.max(58, cjk * 12 + (text.length - cjk) * 7 + 20);
      const h = 22;
      this.svg.appendChild(svgEl("rect", {
        x: x - w / 2, y: y - h / 2, width: w, height: h, rx: 9,
        fill: fill, stroke: color, "stroke-width": "1.6"
      }));
      const label = svgEl("text", {
        x: x, y: y + 4,
        "text-anchor": "middle", "font-size": "12", "font-weight": "800",
        fill: color, "font-family": "ui-monospace,monospace"
      });
      label.textContent = text;
      this.svg.appendChild(label);
    }

    addHit(d, edge) {
      if (this.readonly) return;
      const hit = svgEl("path", {
        d: d, fill: "none", stroke: "transparent", "stroke-width": "14",
        style: "pointer-events:stroke;cursor:pointer"
      });
      hit.addEventListener("click", (e) => {
        e.stopPropagation();
        if (!confirm("删除这条连线？")) return;
        this.edges = this.edges.filter((x) => x.id !== edge.id);
        this.changed();
      });
      this.svg.appendChild(hit);
    }

    anchor(rect, toward) {
      const cx = rect.x + rect.w / 2;
      const cy = rect.y + rect.h / 2;
      const dx = toward.x - cx;
      const dy = toward.y - cy;
      if (dx === 0 && dy === 0) return { x: cx, y: cy };
      const sx = dx === 0 ? Infinity : (rect.w / 2) / Math.abs(dx);
      const sy = dy === 0 ? Infinity : (rect.h / 2) / Math.abs(dy);
      const s = Math.min(sx, sy);
      return { x: cx + dx * s, y: cy + dy * s };
    }

    renderInspector() {
      const box = this.inspector;
      if (!box) return;
      box.innerHTML = "";
      const node = this.nodeById(this.selectedId);
      if (!node) {
        const tip = document.createElement("p");
        tip.className = "text-xs text-slate-400 leading-relaxed";
        tip.textContent = this.readonly
          ? "这是学生画出的模块图：方块是函数模块，箭头表示调用与参数传递。"
          : "点一个模块来修改它的名字、参数和说明；点「连线」再依次点两个模块，就能画出调用箭头。";
        box.appendChild(tip);
        return;
      }

      const title = document.createElement("h3");
      title.className = "text-sm font-extrabold text-violet-600 mb-2";
      title.textContent = node.kind === "main" ? "main() 主模块" : "函数模块";
      box.appendChild(title);

      const field = (labelText, key, placeholder, disabled) => {
        const wrap = document.createElement("label");
        wrap.className = "block mb-2";
        const lab = document.createElement("span");
        lab.className = "block text-[11px] text-slate-400 font-bold mb-1";
        lab.textContent = labelText;
        const input = document.createElement("input");
        input.type = "text";
        input.value = node[key];
        input.placeholder = placeholder;
        input.disabled = !!disabled || this.readonly;
        input.className = "w-full px-2.5 py-1.5 rounded-xl border-2 border-violet-100 focus:border-violet-400 outline-none text-xs bg-white/90 disabled:bg-slate-50";
        input.addEventListener("input", () => {
          node[key] = input.value;
          // 只重画画布，不重建检查器，否则输入框会失去焦点
          this.renderNodes();
          this.renderEdges();
          this.updateSelectionStyles();
          this.scheduleSave();
        });
        wrap.appendChild(lab);
        wrap.appendChild(input);
        box.appendChild(wrap);
      };

      field("函数名", "name", "如 getInputs", node.kind === "main");
      field("参数（写在 def 括号里）", "params", "如 proA, proB, n");
      field("返回值（给变量接收）", "returns", "如 winsA, winsB");
      field("一句话说明", "desc", "如 模拟 n 局比赛");

      if (!this.readonly) {
        const del = document.createElement("button");
        del.type = "button";
        del.className = "mt-1 w-full px-3 py-2 rounded-xl bg-red-50 text-red-500 text-xs font-bold hover:bg-red-100 transition";
        del.textContent = "🗑️ 删除这个模块";
        del.disabled = node.kind === "main";
        del.style.opacity = node.kind === "main" ? ".4" : "1";
        del.addEventListener("click", () => {
          if (node.kind === "main") return;
          if (!confirm("删除模块「" + node.name + "」以及和它相连的线？")) return;
          this.nodes = this.nodes.filter((n) => n.id !== node.id);
          this.edges = this.edges.filter((e) => e.from !== node.id && e.to !== node.id);
          this.selectedId = null;
          this.changed();
        });
        box.appendChild(del);
      }

      // 连线清单
      const related = this.edges.filter((e) => e.from === node.id || e.to === node.id);
      if (related.length) {
        const h = document.createElement("h4");
        h.className = "text-[11px] text-slate-400 font-bold mt-3 mb-1";
        h.textContent = "相关连线";
        box.appendChild(h);
        related.forEach((e) => {
          const row = document.createElement("div");
          row.className = "flex items-center justify-between gap-1 text-[11px] bg-violet-50 rounded-lg px-2 py-1 mb-1";
          const from = this.nodeById(e.from);
          const to = this.nodeById(e.to);
          const span = document.createElement("span");
          span.className = "truncate text-slate-600";
          span.textContent = (from ? from.name : "?") + " → " + (to ? to.name : "?") +
                            (e.label ? "（" + e.label + "）" : "");
          row.appendChild(span);
          if (!this.readonly) {
            const x = document.createElement("button");
            x.type = "button";
            x.className = "text-red-400 font-bold shrink-0";
            x.textContent = "✕";
            x.addEventListener("click", () => {
              this.edges = this.edges.filter((it) => it.id !== e.id);
              this.changed();
            });
            row.appendChild(x);
          }
          box.appendChild(row);
        });
      }
    }

    updateLinkButton() {
      if (!this.linkBtn) return;
      this.linkBtn.textContent = this.linkMode ? "🔗 连线模式：开" : "🔗 连线模式：关";
      this.linkBtn.style.background = this.linkMode ? "#f59e0b" : "";
      this.linkBtn.style.color = this.linkMode ? "#fff" : "";
    }

    /* ---------- 交互 ---------- */
    bind() {
      const addBtn = this.root.querySelector('[data-act="add-func"]');
      if (addBtn) addBtn.addEventListener("click", () => this.addNode());
      const saveBtn = this.root.querySelector('[data-act="save"]');
      if (saveBtn) saveBtn.addEventListener("click", () => this.saveNow(true));
      const addEdgeBtn = this.root.querySelector('[data-act="add-edge"]');
      if (addEdgeBtn) addEdgeBtn.addEventListener("click", () => this.addEdge());
      if (this.linkBtn) {
        this.linkBtn.addEventListener("click", () => {
          this.linkMode = !this.linkMode;
          this.linkFrom = null;
          this.updateLinkButton();
          this.updateSelectionStyles();
          this.setStatus(this.linkMode ? "连线模式：先点起点模块，再点终点模块" : "");
        });
      }
      this.canvas.addEventListener("pointerdown", (e) => {
        if (e.target === this.canvas || e.target === this.svg) {
          this.selectedId = null;
          this.renderInspector();
          this.renderNodes();
          this.renderEdges();
        }
      });
      window.addEventListener("resize", () => this.renderEdges());
    }

    onNodePointerDown(e, node) {
      e.stopPropagation();
      if (this.readonly) return;
      if (this.linkMode) {
        this._linkHandled = node.id;
        this._linkHandledAt = Date.now();
        this.handleLink(node);
        return;
      }

      this.selectNode(node);

      const startX = e.clientX;
      const startY = e.clientY;
      const originX = node.x;
      const originY = node.y;
      let moved = false;

      const onMove = (ev) => {
        const dx = ev.clientX - startX;
        const dy = ev.clientY - startY;
        if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
        const maxX = Math.max(0, this.canvas.clientWidth - NODE_W);
        node.x = Math.min(Math.max(0, originX + dx), maxX);
        node.y = Math.max(0, originY + dy);
        if (node._el) {
          node._el.style.left = node.x + "px";
          node._el.style.top = node.y + "px";
        }
        this.renderEdges();
      };
      const onUp = () => {
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
        if (moved) this.changed();
      };
      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp);
    }

    onNodeClick(e, node) {
      e.stopPropagation();
      if (this.readonly || !this.linkMode) return;
      // 同一次点击的 pointerdown 已经处理过，避免重复连线/取消
      if (this._linkHandled === node.id && Date.now() - this._linkHandledAt < 600) return;
      this.handleLink(node);
    }

    selectNode(node) {
      this.selectedId = node.id;
      this.renderInspector();
      this.updateSelectionStyles();
      this.renderEdges();
      this.setStatus("已选中「" + node.name + "」，可在右侧修改");
    }

    handleLink(node) {
      if (!this.linkFrom) {
        this.linkFrom = node.id;
        this.updateSelectionStyles();
        this.setStatus("已选起点「" + node.name + "」，再点终点模块");
        return;
      }
      if (this.linkFrom === node.id) {
        this.linkFrom = null;
        this.updateSelectionStyles();
        this.setStatus("已取消起点，重新点两个模块连线");
        return;
      }
      const from = this.linkFrom;
      const label = this.linkLabelInput ? this.linkLabelInput.value.trim() : "";
      const fromNode = this.nodeById(from);
      if (this.edges.some((x) => x.from === from && x.to === node.id)) {
        this.setStatus("这两个模块之间已经有连线了");
      } else {
        this.edges.push({ id: uid("e"), from: from, to: node.id, label: label });
        this.setStatus("已连线：" + (fromNode ? fromNode.name : "?") + " → " + node.name);
      }
      this.linkFrom = null;
      if (this.linkLabelInput) this.linkLabelInput.value = "";
      this.changed();
    }

    updateSelectionStyles() {
      this.nodes.forEach((n) => {
        if (!n._el) return;
        if (this.linkFrom === n.id) {
          n._el.style.outline = "3px solid #f59e0b";
        } else if (this.selectedId === n.id) {
          n._el.style.outline = "3px dashed #f472b6";
        } else {
          n._el.style.outline = "none";
        }
      });
    }

    addEdge() {
      if (!this.fromSel || !this.toSel) return;
      const from = this.fromSel.value;
      const to = this.toSel.value;
      if (!from || !to) return;
      if (from === to) {
        this.setStatus("起点和终点不能是同一个模块");
        return;
      }
      const fromNode = this.nodeById(from);
      const toNode = this.nodeById(to);
      if (this.edges.some((x) => x.from === from && x.to === to)) {
        this.setStatus("这两个模块之间已经有连线了");
        return;
      }
      const label = this.paramInput ? this.paramInput.value.trim() : "";
      this.edges.push({ id: uid("e"), from: from, to: to, label: label });
      if (this.paramInput) this.paramInput.value = "";
      this.setStatus("已连线：" + (fromNode ? fromNode.name : "?") + " → " + (toNode ? toNode.name : "?"));
      this.changed();
    }

    addNode() {
      const count = this.nodes.filter((n) => n.kind !== "main").length;
      let name = "func" + (count + 1);
      let i = count + 1;
      while (this.nodes.some((n) => n.name === name)) {
        i += 1;
        name = "func" + i;
      }
      // 在 3 列 × 2 行的空位里找位置，避免模块叠在一起
      const funcs = this.nodes.filter((n) => n.kind !== "main");
      let pos = null;
      for (let row = 0; row < 2 && !pos; row += 1) {
        for (let col = 0; col < 3 && !pos; col += 1) {
          const spot = { x: 20 + col * (NODE_W + 34), y: 300 + row * 130 };
          const taken = funcs.some((n) => Math.abs(n.x - spot.x) < 60 && Math.abs(n.y - spot.y) < 60);
          if (!taken) pos = spot;
        }
      }
      const node = {
        id: uid("n"), kind: "func", name: name,
        params: "", returns: "", desc: "",
        x: pos ? pos.x : 20 + (count % 3) * (NODE_W + 34),
        y: pos ? pos.y : 300
      };
      this.nodes.push(node);
      this.selectedId = node.id;
      this.changed();
    }

    changed() {
      this.renderNodes();
      this.renderEdges();
      this.renderInspector();
      this.refreshSelects();
      this.scheduleSave();
    }

    setStatus(text) {
      if (this.statusEl) this.statusEl.textContent = text || "";
    }

    scheduleSave() {
      if (this.readonly || !this.saveUrl) return;
      this.setStatus("编辑中…");
      clearTimeout(this.saveTimer);
      this.saveTimer = setTimeout(() => this.saveNow(false), 1200);
    }

    async saveNow(manual) {
      if (this.readonly || !this.saveUrl) return null;
      clearTimeout(this.saveTimer);
      this.setStatus("保存中…");
      try {
        const resp = await fetch(this.saveUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ diagram: this.toJSON() })
        });
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        const data = await resp.json();
        const now = new Date();
        const hh = String(now.getHours()).padStart(2, "0");
        const mm = String(now.getMinutes()).padStart(2, "0");
        this.setStatus("已保存 " + hh + ":" + mm + (manual ? "" : "（自动）"));
        this.root.dispatchEvent(new CustomEvent("design-saved", { detail: data }));
        return data;
      } catch (err) {
        this.setStatus("保存失败，请点「保存」重试");
        return null;
      }
    }
  }

  function boot() {
    document.querySelectorAll("[data-module-canvas]").forEach((root) => {
      if (!root.designer) new ModuleDesigner(root);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
