/* 代码原创性检测（AI 率）+ 超纲答辩。
 *
 * 用法：
 *   <div data-ai-check data-check-url="..." data-defense-url="..."
 *        data-submitted="1" data-check='{...}'>
 *     <span data-check-status></span>
 *     <div data-check-body></div>
 *     <div data-check-empty></div>
 *   </div>
 *
 * 行为：
 *   1. 已提交代码但还没有检测记录 → 页面加载后自动调用检测；
 *   2. 有超纲/高 AI 率 → 出答辩题，答题框禁止粘贴（拦截并计数，作为教师参考）；
 *   3. 提交答辩 → AI 判断是否真正理解，结果展示给师生双方。
 */
(function () {
  "use strict";

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function tone(rate) {
    if (rate >= 70) return { label: "偏高", chip: "bg-red-100 text-red-600", bar: "bg-red-400" };
    if (rate >= 45) return { label: "中等", chip: "bg-amber-100 text-amber-700", bar: "bg-amber-400" };
    return { label: "较低", chip: "bg-green-100 text-green-600", bar: "bg-green-400" };
  }

  function init() {
    document.querySelectorAll("[data-ai-check]").forEach(setup);
  }

  function setup(root) {
    const checkUrl = root.dataset.checkUrl || "";
    const defenseUrl = root.dataset.defenseUrl || "";
    const submitted = root.dataset.submitted === "1";
    const body = root.querySelector("[data-check-body]");
    const empty = root.querySelector("[data-check-empty]");
    const status = root.querySelector("[data-check-status]");
    if (!body) return;

    let view = null;
    try {
      view = JSON.parse(root.dataset.check || "");
    } catch (e) {
      view = null;
    }
    if (!view || typeof view.ai_rate !== "number") view = null;

    let pasteCount = 0;

    function setStatus(text) {
      if (status) status.textContent = text || "";
    }

    function showBody() {
      body.classList.remove("hidden");
      if (empty) empty.classList.add("hidden");
    }

    function render(v) {
      const rate = Math.max(0, Math.min(100, Number(v.ai_rate) || 0));
      const t = tone(rate);
      const signals = Array.isArray(v.signals) ? v.signals : [];
      const scope = Array.isArray(v.out_of_scope) ? v.out_of_scope : [];
      const questions = Array.isArray(v.questions) ? v.questions : [];
      const defense = v.defense || {};
      const answered = !!v.answered_at;

      let html = "";
      html += '<div class="flex items-end gap-3 flex-wrap">';
      html += '<div class="text-4xl font-extrabold text-slate-700 leading-none">' + rate +
              '<span class="text-base font-bold">%</span></div>';
      html += '<span class="px-2 py-0.5 rounded-full text-[11px] font-bold ' + t.chip +
              '">AI 参与度：' + t.label + '</span>';
      if (v.checked_at) {
        html += '<span class="text-[11px] text-slate-400">检测于 ' + esc(v.checked_at) + '</span>';
      }
      html += "</div>";
      html += '<div class="mt-2 h-2 rounded-full bg-slate-100 overflow-hidden">' +
              '<div class="h-full ' + t.bar + '" style="width:' + rate + '%"></div></div>';
      if (v.summary) {
        html += '<p class="text-xs text-slate-500 mt-2">' + esc(v.summary) + "</p>";
      }

      if (signals.length) {
        html += '<div class="mt-3"><div class="text-[11px] font-bold text-slate-400 mb-1">检测依据</div><ul class="text-xs text-slate-600 space-y-1">';
        signals.forEach(function (s) {
          html += '<li>· ' + esc(s) + "</li>";
        });
        html += "</ul></div>";
      }

      if (scope.length) {
        html += '<div class="mt-3"><div class="text-[11px] font-bold text-slate-400 mb-1">超出已学范围 / 疑点</div><div class="space-y-2">';
        scope.forEach(function (item) {
          html += '<div class="rounded-2xl bg-amber-50 border-2 border-amber-100 p-3">';
          html += '<div class="text-xs font-bold text-amber-700">⚠️ ' + esc(item.concept || "疑点") +
                  (item.why ? '<span class="font-normal text-amber-600"> · ' + esc(item.why) + "</span>" : "") +
                  "</div>";
          if (item.snippet) {
            html += '<pre class="mt-1 text-[11px] bg-white rounded-xl p-2 overflow-auto whitespace-pre-wrap text-slate-600">' +
                    esc(item.snippet) + "</pre>";
          }
          html += "</div>";
        });
        html += "</div></div>";
      }

      if (answered) {
        const u = Math.max(0, Math.min(100, Number(v.understanding) || 0));
        const ut = u >= 70 ? { c: "text-green-600", chip: "bg-green-100 text-green-600" }
                 : u >= 40 ? { c: "text-amber-600", chip: "bg-amber-100 text-amber-700" }
                 : { c: "text-red-500", chip: "bg-red-100 text-red-600" };
        html += '<div class="mt-4 rounded-3xl bg-slate-50 border-2 border-slate-200 p-4">';
        html += '<div class="flex items-center gap-2 flex-wrap"><span class="text-xs font-bold text-slate-500">答辩结果</span>';
        html += '<span class="px-2 py-0.5 rounded-full text-[11px] font-bold ' + ut.chip + '">' +
                esc(defense.verdict || "已提交") + " · 理解度 " + u + "%</span>";
        if (v.answered_at) {
          html += '<span class="text-[11px] text-slate-400">' + esc(v.answered_at) + "</span>";
        }
        html += "</div>";
        if (defense.comment) {
          html += '<p class="text-xs text-slate-600 mt-2">' + esc(defense.comment) + "</p>";
        }
        if (Array.isArray(defense.per_question) && defense.per_question.length) {
          html += '<ul class="mt-2 space-y-1 text-[11px]">';
          defense.per_question.forEach(function (p) {
            html += '<li class="' + (p.ok ? "text-green-600" : "text-red-500") + '">' +
                    (p.ok ? "✓" : "✗") + " 第 " + esc(p.index) + " 题：" + esc(p.comment || "") + "</li>";
          });
          html += "</ul>";
        }
        if ((v.paste_count || 0) > 0) {
          html += '<p class="mt-2 text-[11px] text-amber-700">⚠️ 作答期间尝试粘贴 ' + v.paste_count +
                  " 次（已被拦截，记录供老师参考）。</p>";
        }
        html += "</div>";
      } else if (questions.length) {
        html += '<div class="mt-4 rounded-3xl bg-rose-50/60 border-2 border-rose-100 p-4">';
        html += '<div class="text-sm font-extrabold text-rose-500 mb-1">🎤 请用自己的话解释这些代码</div>';
        html += '<p class="text-[11px] text-slate-500 mb-3">这里不是考试，答得口语化也可以；只要讲清"它在干什么、不写会怎样"就算懂。粘贴已被禁用。</p>';
        questions.forEach(function (q, i) {
          html += '<div class="mb-3">';
          html += '<div class="text-xs font-bold text-slate-700">' + (i + 1) + ". " + esc(q.question) + "</div>";
          if (q.snippet) {
            html += '<pre class="mt-1 text-[11px] bg-white rounded-xl p-2 overflow-auto whitespace-pre-wrap text-slate-500">' +
                    esc(q.snippet) + "</pre>";
          }
          html += '<textarea data-answer="' + esc(q.id || ("q" + (i + 1))) + '" rows="3" spellcheck="false" ' +
                  'class="mt-2 w-full bg-white rounded-2xl border-2 border-rose-100 p-3 text-xs outline-none focus:border-rose-300 transition" ' +
                  'placeholder="用你自己的话讲讲这段代码…"></textarea>';
          html += "</div>";
        });
        html += '<p data-paste-hint class="hidden text-[11px] text-amber-700 mb-2"></p>';
        html += '<button type="button" data-defense-submit class="px-5 py-2.5 rounded-2xl bg-rose-400 text-white text-sm font-bold shadow hover:bg-rose-500 transition">提交答辩</button>';
        html += "</div>";
      } else {
        html += '<p class="mt-4 text-xs text-green-600">✅ 没有发现需要解释的疑点，保持这种自己动手的写法。</p>';
      }

      body.innerHTML = html;
      showBody();
      bindDefense();
    }

    function bindDefense() {
      const btn = body.querySelector("[data-defense-submit]");
      if (!btn) return;
      const hint = body.querySelector("[data-paste-hint]");
      body.querySelectorAll("[data-answer]").forEach(function (ta) {
        ta.addEventListener("paste", function (e) {
          e.preventDefault();
          pasteCount += 1;
          if (hint) {
            hint.textContent = "检测到粘贴操作（第 " + pasteCount + " 次），已拦截。请用你自己的话作答。";
            hint.classList.remove("hidden");
          }
        });
      });

      btn.addEventListener("click", function () {
        const answers = [];
        body.querySelectorAll("[data-answer]").forEach(function (ta) {
          answers.push({ id: ta.dataset.answer, answer: ta.value.trim() });
        });
        if (!answers.some(function (a) { return a.answer; })) {
          if (hint) {
            hint.textContent = "先写点你自己的解释再提交吧。";
            hint.classList.remove("hidden");
          }
          return;
        }
        btn.disabled = true;
        btn.textContent = "AI 批改中…";
        setStatus("⏳ AI 正在判断你的解释…");
        fetch(defenseUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ answers: answers, paste_count: pasteCount })
        }).then(function (resp) {
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          return resp.json();
        }).then(function (data) {
          setStatus("✅ 答辩已提交");
          render(data);
        }).catch(function (err) {
          btn.disabled = false;
          btn.textContent = "提交答辩";
          setStatus("❌ 提交失败：" + err.message + "，请重试");
        });
      });
    }

    function runCheck() {
      setStatus("⏳ 正在检测代码原创性（约几秒）…");
      if (empty) {
        empty.classList.remove("hidden");
        empty.textContent = "正在检测…";
      }
      fetch(checkUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      }).then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      }).then(function (data) {
        setStatus(data.source === "fallback" ? "规则粗检（未接入 AI）" : "✅ 检测完成");
        render(data);
      }).catch(function (err) {
        setStatus("❌ 检测失败");
        if (empty) {
          empty.textContent = "检测失败（" + err.message + "），刷新页面可重试。";
        }
      });
    }

    if (view) {
      setStatus(vStatus(view));
      render(view);
    } else if (submitted) {
      runCheck();
    } else {
      setStatus("");
    }

    function vStatus(v) {
      if (v.answered_at) return "✅ 已答辩";
      if (Array.isArray(v.questions) && v.questions.length) return "⚠️ 待答辩";
      return "✅ 检测完成";
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
