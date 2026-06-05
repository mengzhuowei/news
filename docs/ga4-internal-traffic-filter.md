# GA4 内部流量过滤配置指南

> 目的：把团队成员、运维监控、本地测试的访问从 GA4 数据中剔除，
> 让"用户数/会话/关键事件"反映真实访客行为。

---

## 1. 思路

GA4 通过 **"数据过滤器"**（Data Filter）按 IP / 开发者模式标记流量。
被过滤的流量会从所有 GA4 报告中排除，但**历史数据不可恢复**。

---

## 2. 方法 A：按 IP 过滤（推荐）

### 步骤
1. 进入 GA4 后台：**管理 → 数据设置 → 数据过滤**
2. 点击 **"创建过滤器"** → 选 **"内部流量"**
3. 填写：
   - **名称**：`内部团队 IP`
   - **过滤器操作**：`排除`
   - **匹配类型**：`IP 地址等于`
4. 添加团队常用 IP 段，例如：
   - `203.0.113.0/24`（办公室公网 IP，**请替换为真实值**）
   - `198.51.100.42`（某位成员家庭宽带）
   - `192.0.2.0/24`（VPN 出口段）

### 如何获取真实 IP？
- 团队成员各自访问 [https://api.ipify.org](https://api.ipify.org) 或 [https://ifconfig.me](https://ifconfig.me)
- 运维监控（UptimeRobot、Datadog 探针等）也会暴露外网 IP

---

## 3. 方法 B：通过 Nginx 给团队访问打标记（更灵活）

在 Nginx 转发请求时，对来自内部 IP 的请求**追加一个特殊 header**，
GA4 通过 `traffic_type` 自定义维度识别并过滤。

### 3.1 Nginx 端：给内部 IP 打 header
在 [`nginx/conf.d/default.conf.template`](../nginx/conf.d/default.conf.template) 的
`server` 块里加：

```nginx
# 内部 IP 段（请改为团队真实段）
geo $is_internal {
    default 0;
    203.0.113.0/24 1;   # 办公室
    198.51.100.42 1;    # 某成员家庭
}

server {
    # ...

    location / {
        # 内部访问打标记，浏览器端 JS 据此跳过 GA
        add_header X-Internal-Traffic $is_internal always;

        # 透传给上游
        proxy_set_header X-Internal-Traffic $is_internal;

        try_files $uri $uri/ /index.html;
    }
}
```

### 3.2 前端：检测 header 并跳过 GA
在 [`shared/templates/jinja2/base.html`](../shared/templates/jinja2/base.html)
`gtag('config', ...)` 之前：

```html
<script>
  // 内部 IP 访问 → 关闭 GA
  (function () {
    var isInternal = {{ 'true' if request and request.headers.get('X-Internal-Traffic') == '1' else 'false' }};
    if (isInternal) {
      window['ga-disable-G-XXXXXXXXXX'] = true;  // ← 替换成你的 GA_ID
    }
  })();
</script>
```

> **注意**：本项目是**纯静态站点**（Nginx 直接吐文件），
> 这个 header 方案不适用，请改用 方法 A。

### 3.3 静态站点的折中方案
在 [base.html](../shared/templates/jinja2/base.html) 的 gtag 初始化前加：

```html
<script>
  (function () {
    // 内部测试用：访问 ?_internal=1 时关闭 GA
    var params = new URLSearchParams(window.location.search);
    if (params.get('_internal') === '1') {
      window['ga-disable-{{ ga_id }}'] = true;
    }
    // 也支持 ?_debug=1 打开 GA Debug View
    if (params.get('_debug') === '1' && typeof gtag === 'function') {
      gtag('config', '{{ ga_id }}', { debug_mode: true });
    }
  })();
</script>
```

团队成员访问时用 `https://yourdomain.com/?_internal=1`，GA 立即停止上报。
Debug 模式用 `?_debug=1`，可在 GA4 DebugView 中实时查看。

---

## 4. 方法 C：按"开发模式"识别（最精确）

如果未来要切到 SPA / SSR，再启用此方法。

- 部署时给生产环境注入 cookie `__dev=1`
- base.html 检测到该 cookie 就 `ga-disable-<id>`
- 本地开发自动开启

---

## 5. 建议落地步骤

1. **P0**：先在 GA4 后台**只配置 1-2 个 office IP** 试运行
2. **P1**：把 `?_internal=1` 推广给团队（无需等运维改 Nginx）
3. **P2**：在 README / 团队 Wiki 里写明"内部访问加 `?_internal=1` 后缀"
4. **P3**：CI/CD 在部署时自动把所有办公室 IP 加入 GA4 过滤器

---

## 6. 验证是否生效

- 访问站点 → 打开浏览器 DevTools → Network → 过滤 `collect` 或 `gtag`
  - 内部访问（带 `?_internal=1`）：**看不到任何 `collect?v=2` 请求**
  - 正常访问：能看到上报
- GA4 DebugView：访问 `?_debug=1` 路径，应立即看到事件

---

## 7. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 过滤后数据完全没变 | 团队都用移动 4G/5G | 让大家发自己外网 IP，列入过滤器 |
| 过滤后总数反而**变多** | 过滤器配反了（"包含"而非"排除"） | 检查过滤器操作 |
| 改了过滤器没效果 | GA4 过滤器**不回溯历史** | 这是正常的，过 24h 后看新数据 |
| 团队成员怀疑自己被过滤 | 检查 `$_GET['_internal']` 是否启用，或是否在 office 内网 | 让他们用手机 4G 测试 |
