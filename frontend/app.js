/* ===== frpC 前端逻辑 ===== */
(function () {
  "use strict";

  var $ = function (sel) { return document.querySelector(sel); };
  var $$ = function (sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); };
  var api = null;
  var editingName = null;   // 编辑模式下的原名称；null 表示新建
  var pollTimer = null;

  /* ---------- 主题 ---------- */
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("frpc-theme", theme); } catch (e) {}
  }
  function initTheme() {
    var saved;
    try { saved = localStorage.getItem("frpc-theme"); } catch (e) {}
    if (!saved) {
      saved = (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) ? "light" : "dark";
    }
    applyTheme(saved);
  }
  function toggleTheme() {
    var cur = document.documentElement.getAttribute("data-theme");
    applyTheme(cur === "dark" ? "light" : "dark");
  }

  /* ---------- 状态栏渲染 ---------- */
  function setStat(id, text, cls) {
    var el = $(id);
    el.querySelector(".stat-value").textContent = text;
    el.classList.remove("ok", "warn", "bad");
    if (cls) el.classList.add(cls);
  }
  function renderState(s) {
    if (!s) return;
    setStat("#stat-version", s.version || "—", null);
    setStat("#stat-link", s.link.text, s.link.ok ? "ok" : (s.link.warn ? "warn" : "bad"));
    setStat("#stat-run", s.run.text, s.run.ok ? "ok" : "bad");
    var latOk = !!(s.latency && s.latency.ms != null);
    setStat("#stat-latency", latOk ? (s.latency.ms + " ms") : "不可达", latOk ? "ok" : "bad");

    // 规则表
    var body = $("#rule-body");
    body.innerHTML = "";
    var rows = s.proxies || [];
    if (rows.length === 0) {
      $("#rule-empty").style.display = "block";
    } else {
      $("#rule-empty").style.display = "none";
      rows.forEach(function (p) {
        var tr = document.createElement("tr");
        tr.dataset.name = p.name;
        tr.innerHTML = "<td>" + esc(p.name) + "</td><td>" + esc(p.type) + "</td><td>" +
          esc(p.local) + "</td><td>" + esc(p.remote) + "</td><td class='tag-" + p.tag + "'>" +
          esc(p.status) + "</td>";
        body.appendChild(tr);
      });
    }

    // 设置页开关状态（随轮询刷新）
    $("#svc-toggle").checked = !!s.service.installed;
    $("#svc-state").textContent = "状态：" + stateText(s.service.state);
    $("#boot-toggle").checked = !!s.boot;
    $("#boot-state").textContent = "状态：" + (s.boot ? "已启用" : "未启用");

    // 回填设置框（仅在输入框非焦点时写入，见 fillSettings）
    fillSettings(s);
  }
  // 回填设置框：仅在对应输入框没有焦点时写入——既保证重启后能恢复已保存值，又不打断用户正在输入的内容
  function fillSettings(s) {
    if (!s || !s.config) return;
    var ae = document.activeElement;
    if (ae !== $("#srv-addr")) $("#srv-addr").value = s.config.addr || "";
    if (ae !== $("#srv-port")) $("#srv-port").value = s.config.port || "";
    if (ae !== $("#srv-token")) $("#srv-token").value = s.config.token || "";
  }
  function stateText(st) {
    return { running: "运行中", stopped: "已停止", not_installed: "未安装", unknown: "未知" }[st] || "—";
  }
  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  /* ---------- 轮询 ---------- */
  function poll() {
    if (!api) return;
    api.get_state().then(function (s) { renderState(s); })
      .catch(function () {});
    // 若当前在日志页，随轮询自动刷新日志
    if ($("#panel-logs").classList.contains("active")) {
      loadLog();
    }
  }
  function startPoll() {
    stopPoll();
    pollTimer = setInterval(poll, 3000);
  }
  function stopPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  /* ---------- 日志 ---------- */
  function loadLog() {
    if (!api) return;
    api.get_log().then(function (r) {
      $("#log-path").textContent = "日志文件：" + (r.path || "—") +
        (r.total_lines != null ? ("　共 " + r.total_lines + " 行，显示最后 500 行") : "");
      var v = $("#log-view");
      v.textContent = r.log || "（暂无日志，启动 frpc 后此处会显示运行日志）";
      v.scrollTop = v.scrollHeight;
    }).catch(function () {});
  }

  /* ---------- 规则操作 ---------- */
  function selectedName() {
    var tr = $("#rule-body").querySelector("tr.selected");
    return tr ? tr.dataset.name : null;
  }
  function getProxy() {
    var proxy = {
      name: $("#p-name").value.trim(),
      type: $("#p-type").value,
      localIP: $("#p-localIP").value.trim(),
      localPort: $("#p-localPort").value.trim(),
      useCompression: $("#p-compress").checked
    };
    var t = proxy.type;
    if (t === "tcp" || t === "udp" || t === "xtcp") {
      proxy.remotePort = $("#p-remotePort").value.trim();
    }
    if (t === "http" || t === "https") {
      proxy.customDomains = $("#p-domain").value.trim();
      proxy.subdomain = $("#p-subdomain").value.trim();
    }
    if (t === "stcp" || t === "xtcp") {
      proxy.secretKey = $("#p-secret").value.trim();
    }
    return proxy;
  }
  function showTypeRows() {
    var t = $("#p-type").value;
    toggle("#row-remote", (t === "tcp" || t === "udp" || t === "xtcp"));
    toggle("#row-domain", (t === "http" || t === "https"));
    toggle("#row-subdomain", (t === "http" || t === "https"));
    toggle("#row-secret", (t === "stcp" || t === "xtcp"));
  }
  function toggle(sel, show) {
    $(sel).classList[show ? "remove" : "add"]("hidden");
  }

  /* ---------- 弹窗 ---------- */
  function openModal(name) {
    editingName = name || null;
    $("#modal-title").textContent = name ? "编辑规则" : "新建规则";
    clearErrors();
    fillFromBackend(name);   // 内部异步填完后才 validate/focus（避免字段未填就报"空"）
    $("#modal").classList.add("show");
  }
  function fillFromBackend(name) {
    // 无论新建还是编辑，都必须等 fillFromBackend 完成后再 validate（异步读后端规则）
    if (!name) {
      $("#p-name").value = ""; $("#p-type").value = "tcp"; $("#p-localIP").value = "127.0.0.1";
      $("#p-localPort").value = ""; $("#p-remotePort").value = ""; $("#p-domain").value = "";
      $("#p-subdomain").value = ""; $("#p-secret").value = ""; $("#p-compress").checked = false;
      showTypeRows();
      validateLive();
      $("#p-name").focus();
      return;
    }
    api.get_proxy(name).then(function (p) {
      if (!p) return;
      $("#p-name").value = p.name || "";
      $("#p-type").value = p.type || "tcp";
      $("#p-localIP").value = p.localIP || "127.0.0.1";
      $("#p-localPort").value = p.localPort != null ? p.localPort : "";
      $("#p-remotePort").value = p.remotePort != null ? p.remotePort : "";
      $("#p-domain").value = Array.isArray(p.customDomains) ? p.customDomains.join(",") : (p.customDomains || "");
      $("#p-subdomain").value = p.subdomain || "";
      $("#p-secret").value = p.secretKey || p.sk || "";
      $("#p-compress").checked = !!p.useCompression;
      showTypeRows();
      validateLive();
      $("#p-name").focus();
    }).catch(function () {});
  }
  function closeModal() {
    $("#modal").classList.remove("show");
    editingName = null;
  }
  function clearErrors() { $("#p-errors").innerHTML = ""; }
  function showErrors(errs) {
    var box = $("#p-errors");
    box.innerHTML = "";
    (errs || []).forEach(function (e) {
      var d = document.createElement("div");
      d.className = "err"; d.textContent = e.msg; box.appendChild(d);
    });
  }
  function validateLive() {
    var proxy = getProxy();
    api.validate_proxy(proxy, editingName).then(function (errs) {
      showErrors(errs);
      $("#modal-ok").disabled = errs.length > 0;
    }).catch(function (e) {
      showErrors([{ msg: "校验失败：" + e }]);
      $("#modal-ok").disabled = true;
    });
  }
  function submitModal() {
    var proxy = getProxy();
    var call = editingName ? api.update_proxy(editingName, proxy) : api.add_proxy(proxy);
    call.then(function (r) {
      if (!r.ok) { showErrors(r.errors || [{ msg: r.msg || "保存失败" }]); return; }
      closeModal();
      poll();
      setFooter((editingName ? "已更新" : "已添加") + "规则『" + proxy.name + "』");
    }).catch(function (e) {
      showErrors([{ msg: "保存失败：" + e }]);
    });
  }

  /* ---------- footer ---------- */
  function setFooter(msg) { $("#footer").textContent = msg; }

  /* ---------- 事件绑定 ---------- */
  function bind() {
    $("#btn-theme").addEventListener("click", toggleTheme);
    $("#btn-refresh").addEventListener("click", function () { poll(); });

    $$(".tab").forEach(function (t) {
      t.addEventListener("click", function () {
        $$(".tab").forEach(function (x) { x.classList.remove("active"); });
        $$(".panel").forEach(function (x) { x.classList.remove("active"); });
        t.classList.add("active");
        $("#panel-" + t.dataset.tab).classList.add("active");
        if (t.dataset.tab === "logs") loadLog();
      });
    });

    // 日志页按钮
    $("#btn-log-refresh").addEventListener("click", function () { loadLog(); setFooter("日志已刷新"); });
    $("#btn-log-clear").addEventListener("click", function () {
      api.clear_log().then(function (r) { setFooter(r.ok ? "日志已清空" : r.msg); loadLog(); });
    });
    $("#log-path").addEventListener("click", function () {
      api.open_log_dir().then(function (r) { if (!r.ok) setFooter(r.msg); });
    });

    $("#rule-body").addEventListener("click", function (e) {
      var tr = e.target.closest("tr");
      if (!tr) return;
      $$("#rule-body tr").forEach(function (x) { x.classList.remove("selected"); });
      tr.classList.add("selected");
    });

    $("#btn-new").addEventListener("click", function () { openModal(null); });
    $("#btn-edit").addEventListener("click", function () {
      var n = selectedName();
      if (!n) { setFooter("请先选中一条规则"); return; }
      openModal(n);
    });
    $("#btn-del").addEventListener("click", function () {
      var n = selectedName();
      if (!n) { setFooter("请先选中一条规则"); return; }
      if (confirm("确定删除规则『" + n + "』？")) {
        api.delete_proxy(n).then(function () { poll(); setFooter("已删除规则『" + n + "』"); });
      }
    });

    $("#btn-start").addEventListener("click", function () {
      api.start().then(function (r) { setFooter(r.ok ? "已启动" : r.msg); poll(); });
    });
    $("#btn-stop").addEventListener("click", function () {
      api.stop().then(function (r) { setFooter(r.ok ? "已停止" : r.msg); poll(); });
    });
    $("#btn-reload").addEventListener("click", function () {
      api.reload_config().then(function (r) { setFooter(r.ok ? "已重载" : r.msg); poll(); });
    });

    // 设置页
    $("#btn-save-common").addEventListener("click", function () {
      api.save_common($("#srv-addr").value.trim(), $("#srv-port").value.trim(), $("#srv-token").value)
        .then(function (r) { setFooter(r.ok ? "服务器配置已保存" : r.msg); poll(); });
    });
    $("#svc-toggle").addEventListener("change", function () {
      api.toggle_service(this.checked).then(function (r) {
        if (!r.ok) this.checked = !this.checked;
        setFooter(r.ok ? (this.checked ? "已安装系统服务" : "已卸载系统服务") : r.msg);
        poll();
      }.bind(this));
    });
    $("#boot-toggle").addEventListener("change", function () {
      api.toggle_boot(this.checked).then(function (r) {
        if (!r.ok) this.checked = !this.checked;
        setFooter(r.ok ? (this.checked ? "已开启开机自启" : "已关闭开机自启") : r.msg);
        poll();
      }.bind(this));
    });
    $("#btn-check-mgmt").addEventListener("click", function () {
      api.check_mgmt().then(function (s) { renderState(s); setFooter("状态已刷新"); });
    });

    // 弹窗
    $("#modal-close").addEventListener("click", closeModal);
    $("#modal-cancel").addEventListener("click", closeModal);
    $("#modal-ok").addEventListener("click", submitModal);
    $("#modal").addEventListener("click", function (e) { if (e.target === $("#modal")) closeModal(); });
    $("#p-type").addEventListener("change", function () { showTypeRows(); validateLive(); });
    ["#p-name", "#p-localIP", "#p-localPort", "#p-remotePort", "#p-domain", "#p-subdomain", "#p-secret"]
      .forEach(function (s) { $(s).addEventListener("input", validateLive); });
  }

  /* ---------- 初始化 ---------- */
  function init() {
    initTheme();
    bind();
    startPoll();
    // 首次渲染（renderState 内部会回填设置框）
    api.get_state().then(renderState).catch(function () {});
    setFooter("就绪");
  }
  function waitForApi(attempt) {
    attempt = attempt || 0;
    if (window.pywebview && window.pywebview.api) {
      api = window.pywebview.api;
      init();
    } else if (attempt < 60) {
      setTimeout(function () { waitForApi(attempt + 1); }, 50);
    } else {
      setFooter("错误：前端未能连接到后端");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { waitForApi(); });
  } else {
    waitForApi();
  }
})();
