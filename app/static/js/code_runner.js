/* 学生代码在线运行：
 *   优先用 Pyodide（WebAssembly 版 Python）在浏览器里真正执行；
 *   如果浏览器加载不了 Pyodide（网络/CDN 被拦），自动改为调用后端 AI 推测运行结果。
 *
 * input() 支持：浏览器里的 Python 没有真键盘，所以运行前把「运行输入」里的多行文本
 *   注入成 stdin（setStdin），并同时把 builtins.input 替换成按行取值的函数。
 *
 * 用法：
 *   <div data-code-runner data-code-editor="code"
 *        data-fallback-url="/student/projects/1/run">
 *     <button data-run-btn>▶ 运行看看</button>
 *     <span data-run-status></span>
 *     <textarea data-run-input></textarea>
 *     <div data-run-output class="hidden"></div>
 *   </div>
 */
(function () {
  "use strict";

  const VERSION = "0.26.4";
  const BASE = "https://cdn.jsdelivr.net/pyodide/v" + VERSION + "/full/";
  const LOAD_TIMEOUT_MS = 15000;
  const RUN_TIMEOUT_MS = 20000;

  const WORKER_CODE = `
importScripts("${BASE}pyodide.js");
let pyodide = null;
self.onmessage = async (e) => {
  const id = e.data.id;
  const code = e.data.code || "";
  const prelude = e.data.prelude || "";
  const stdin = e.data.stdin || [];
  let out = "";
  const push = (s) => { if (out.length < 20000) out += s + "\\n"; };
  try {
    if (!pyodide) pyodide = await loadPyodide({ indexURL: "${BASE}" });
    self.postMessage({ __id: id, stage: "ready" });
    pyodide.setStdout({ batched: push });
    pyodide.setStderr({ batched: push });
    if (stdin.length) {
      try {
        let i = 0;
        pyodide.setStdin({
          stdin: () => (i < stdin.length ? String(stdin[i++]) + "\\n" : null)
        });
      } catch (err) { /* 旧版 Pyodide 没有 setStdin，靠 prelude 的 input 补丁兜底 */ }
    }
    if (prelude) await pyodide.runPythonAsync(prelude);
    await pyodide.runPythonAsync(code);
    self.postMessage({ __id: id, ok: true, out: out });
  } catch (err) {
    self.postMessage({ __id: id, ok: false, out: out, err: String(err && err.message ? err.message : err) });
  }
};
`;

  /* 把输入按行做成 Python 代码：覆盖 builtins.input，模拟真实键盘输入。 */
  function buildPrelude(inputs) {
    return [
      "import builtins",
      "_RUN_INPUTS = " + JSON.stringify(inputs || []),
      "def _run_input(prompt=''):",
      "    if prompt:",
      "        print(prompt, end='')",
      "    if not _RUN_INPUTS:",
      "        raise EOFError('输入已用完：请在「运行输入」里每行填一个值')",
      "    return _RUN_INPUTS.pop(0)",
      "builtins.input = _run_input",
      ""
    ].join("\n");
  }

  let worker = null;
  let seq = 0;
  const pending = {};

  function settle(id, payload) {
    const p = pending[id];
    if (!p) return;
    clearTimeout(p.timer);
    delete pending[id];
    p.onResult(payload);
  }

  function handleMessage(e) {
    const data = e.data || {};
    const p = pending[data.__id];
    if (!p) return;
    if (data.stage === "ready") {
      clearTimeout(p.timer);
      p.timer = setTimeout(function () {
        settle(data.__id, { ok: false, err: "运行超时（20 秒），可能写了死循环。" });
      }, RUN_TIMEOUT_MS);
      if (p.onReady) p.onReady();
      return;
    }
    settle(data.__id, data);
  }

  function dropWorker() {
    if (!worker) return;
    try { worker.terminate(); } catch (e) { /* ignore */ }
    worker = null;
  }

  function ensureWorker() {
    if (worker) return worker;
    const blob = new Blob([WORKER_CODE], { type: "application/javascript" });
    worker = new Worker(URL.createObjectURL(blob));
    worker.onmessage = handleMessage;
    worker.onerror = function () {
      Object.keys(pending).forEach(function (id) {
        settle(id, { loadFail: true });
      });
      dropWorker();
    };
    return worker;
  }

  function runCode(code, inputs, onReady, onResult) {
    const id = ++seq;
    const w = ensureWorker();
    pending[id] = {
      onReady: onReady,
      onResult: function (data) {
        if (data.loadFail) dropWorker();
        onResult(data);
      },
      timer: setTimeout(function () {
        settle(id, { loadFail: true });
      }, LOAD_TIMEOUT_MS)
    };
    w.postMessage({
      id: id,
      code: code,
      stdin: inputs || [],
      prelude: buildPrelude(inputs)
    });
  }

  function lastErrorLine(text) {
    const lines = (text || "").split("\n").map(function (s) { return s.trim(); })
      .filter(Boolean);
    for (let i = lines.length - 1; i >= 0; i -= 1) {
      if (/^[A-Za-z_.]+(Error|Exception|Interrupt)\b/.test(lines[i])) return lines[i];
    }
    return lines.length ? lines[lines.length - 1] : "";
  }

  function withHint(message) {
    if (/cannot unpack non-iterable NoneType/.test(message)) {
      return message + "\n\n提示：某个函数只写了 pass、没有返回值，而 main() 里却在接收它的返回值。" +
             "先补全那个函数的 return，再运行一次。";
    }
    if (/input\(\)|EOFError|Errno 29|I\/O error/.test(message)) {
      return message + "\n\n提示：代码里用了 input()，请在代码下方「需要 input()？」里" +
             "每行填一个输入值，再点运行。";
    }
    return message;
  }

  function init() {
    document.querySelectorAll("[data-code-runner]").forEach(function (root) {
      const btn = root.querySelector("[data-run-btn]");
      const out = root.querySelector("[data-run-output]");
      const status = root.querySelector("[data-run-status]");
      const ta = document.getElementById(root.dataset.codeEditor || "code");
      const inputTa = root.querySelector("[data-run-input]");
      const fallbackUrl = root.dataset.fallbackUrl || "";
      if (!btn || !out || !ta) return;

      function readInputs() {
        if (!inputTa) return [];
        const lines = inputTa.value.split("\n").map(function (s) { return s.trim(); });
        while (lines.length && lines[lines.length - 1] === "") lines.pop();
        return lines;
      }

      function show(text, color) {
        out.classList.remove("hidden");
        out.style.color = color || "#475569";
        out.textContent = text;
      }

      function renderResult(data, aiMode) {
        btn.disabled = false;
        if (data.ok) {
          out.style.color = "#0f172a";
          out.textContent = (data.out && data.out.trim())
            ? data.out
            : (aiMode ? "（推测：运行成功，没有输出）" : "（运行成功，没有输出）");
          if (status) status.textContent = aiMode ? "🤖 AI 推测结果" : "✅ 运行结束";
        } else {
          out.style.color = "#b91c1c";
          const full = data.err || "运行出错";
          const last = lastErrorLine(full) || full;
          const body = ((data.out || "") + (data.out ? "\n" : "") +
            "❌ " + withHint(last)).trim();
          out.textContent = body + (full && full.trim() !== last
            ? "\n\n—— 完整报错 ——\n" + full
            : "");
          if (status) status.textContent = aiMode ? "🤖 AI 推测结果" : "";
        }
        if (aiMode) {
          out.textContent += "\n\n（浏览器运行环境不可用，以上为 AI 按 Python 语义推测的结果，仅供教学参考）";
        }
      }

      function aiFallback(code, inputs) {
        if (!fallbackUrl) {
          btn.disabled = false;
          show("❌ 浏览器运行环境加载失败，且没有配置 AI 兜底运行。", "#b91c1c");
          return;
        }
        show("⏳ 浏览器运行环境不可用，正在让 AI 推测运行结果…", "#475569");
        if (status) status.textContent = "";
        fetch(fallbackUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: code, inputs: inputs })
        }).then(function (resp) {
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          return resp.json();
        }).then(function (data) {
          renderResult({ ok: data.ok, out: data.output, err: data.error }, true);
        }).catch(function (err) {
          btn.disabled = false;
          show("❌ AI 模拟运行失败：" + err.message + "，请检查网络后重试。", "#b91c1c");
        });
      }

      btn.addEventListener("click", function () {
        const code = ta.value;
        if (!code.trim()) {
          show("代码还是空的，先写点内容再运行～", "#b45309");
          return;
        }
        show("⏳ 正在准备 Python 运行环境（首次需要几秒）…", "#475569");
        if (status) status.textContent = "";
        btn.disabled = true;

        runCode(code, readInputs(), function () {
          show("⏳ 正在运行…", "#475569");
        }, function (data) {
          if (data.loadFail) {
            aiFallback(code, readInputs());
            return;
          }
          renderResult(data, false);
        });
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
