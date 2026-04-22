# 方案 A · 配置手册

**架构**:GitHub Actions 跑数据管道 + 算三策略 → Claude Code Routine 做 AI 消息面分析 → 全部推到同一个 **private** repo。

```
GitHub private repo(代码 + 市场数据 + 持仓 + 报告)
      ▲                              ▲
      │ push 骨架 + 止盈分析           │ push AI 消息面
      │                              │
 15:30 ┌─────────────┐          16:00 ┌──────────────┐
      │ GitHub Actions│               │ Claude Routine │
      │ 纯数据 + 策略  │               │ 只做 AI 分析    │
      └──────────────┘               └────────────────┘
```

**持仓保密说明**:`config/positions.json` 在 **private repo** 里,只有你自己能看。如果你对此不放心,先读一遍 README 里的说明,或者回去选方案 B(纯抄底/波段,不处理持仓)。

---

## 配置流程(约 20 分钟)

### Step 1 · 前置软件

- **Git for Windows**:https://git-scm.com/download/win (安装选 "Use Git from Windows Command Prompt")
- **Python 3.11+**:https://www.python.org/downloads/windows/ (勾选 "Add Python to PATH")

验证:打开 cmd 输入 `python --version` 和 `git --version` 都应有输出。

### Step 2 · 创建 GitHub private repo

1. 打开 https://github.com/new
2. Repository name: `astock_daily_analysis`
3. **Private**(必选)
4. 不勾选 "Add a README / .gitignore / license"(保持空白)
5. 点 Create repository

### Step 3 · 本地配置

解压 zip 到 `C:\Users\你\Documents\astock_daily_analysis`,打开 cmd:

```cmd
cd C:\Users\你\Documents\astock_daily_analysis
```

**改两个文件**:

**`config/stocks.yml`** — 替换 `watchlist` 下的股票为你关注的(必改)

**`config/positions.json`** — 写你真实的持仓;没有持仓就写空数组:
```json
{
  "positions": [
    {"code": "600519", "cost_price": 1680.00, "shares": 100, "entry_date": "2025-11-20"}
  ]
}
```

### Step 4 · 推到 GitHub

```cmd
cd C:\Users\你\Documents\astock_daily_analysis

git init
git branch -M main
git add .

REM 检查即将 push 的内容
git status

git commit -m "init: 方案 A 架构"

REM 替换成你的 GitHub 用户名
git remote add origin https://github.com/YOUR_USERNAME/astock_daily_analysis.git
git push -u origin main
```

如果 push 时要你登录,用 GitHub username + **Personal Access Token**(不是密码)。PAT 生成:GitHub → Settings → Developer settings → Personal access tokens → Generate new token (classic) → 勾选 `repo` → Copy。

### Step 5 · 验证 GitHub Actions

1. 打开 repo 的 Actions 标签
2. 看到 "Daily Data Pipeline" 工作流
3. 点进去 → Run workflow → 选 main 分支 → Run
4. 等 5 分钟,应该看到绿色 ✓

**首次可能部分失败**(akshare 冷启动遇限流),这是正常的。点 "Re-run failed jobs",缓存会逐渐建起来,2-3 次后稳定。如果连续 3 次都失败,把日志发给我。

### Step 6 · 验证 Actions 的产出

Actions 成功后,切回 repo 的 Code 标签,应该看到:
- `data/YYYY-MM-DD/signals.json` ← 信号数据
- `data/history/*.json` ← 历史 K 线缓存
- `reports/YYYY-MM-DD.md` ← Markdown 骨架(包含三策略,含止盈)
- `reports/YYYY-MM-DD_dashboard.html` ← HTML 仪表盘

### Step 7 · 配置 Claude Code Routine

1. 打开 https://claude.ai/code/routines(需要 Claude Pro 或 Max 订阅)
2. 点 "New routine"
3. **Trigger**:
   - Type: Schedule
   - Cron: `0 8 * * 1-5`(UTC 08:00 = 北京 16:00)
4. **Prompt**:打开本仓库的 `docs/routine_prompt.md`,整个文件内容贴进 Prompt 框
   - **记得替换 `YOUR_USERNAME` 为你的 GitHub 用户名**(文件里有两处)
5. **Integrations**:
   - GitHub connector → 授权 → 勾选 `astock_daily_analysis`
6. **Test first run**:
   - 点 "Run now"
   - 看 session 日志,约 2-5 分钟
   - 成功后,repo 里 `reports/YYYY-MM-DD.md` 应该已经填入了消息面分析

---

## 日常使用

每个交易日 **什么都不用做**,15:30 Actions 自动跑、16:00 Routine 自动跑。

**想看报告时**:
1. 打开 GitHub repo → `reports/`
2. 点最新日期的 `.md` → 看文字报告
3. 或点 `_dashboard.html` → **右上角 "Raw"** → 在浏览器地址栏前面加 `https://htmlpreview.github.io/?`→ 回车就能直接渲染
   - 或点右上角 "Download" → 本地打开

**更新持仓**(买卖后):
1. 打开 `config/positions.json`,改成本价 / 股数
2. commit & push

---

## 初次调试检查清单

按顺序验证:

- [ ] GitHub repo 是 **Private**(Settings 页面左下确认)
- [ ] `config/positions.json` 是你真实持仓(不是示例贵州茅台 1680)
- [ ] Actions 能跑绿,`reports/*.md` 产出
- [ ] 报告里 **止盈分析区块存在**(对比看 `data/YYYY-MM-DD/signals.json` 里有 `strategy_take_profit` 字段)
- [ ] Routine 跑完 `reports/*.md` 里的 `<!-- CLAUDE_FILL_START -->` 标记消失,变成真实 AI 分析

---

## 常见问题

**Q: Actions 失败 `RemoteDisconnected`**
A: 东财限流。点 "Re-run failed jobs",下一次会利用已建立的缓存,成功率会提升。

**Q: Routine 说 "skeleton report not found"**
A: 说明 Actions 今天还没跑或失败了。先去 Actions 检查。

**Q: Routine 说 "permission denied"**
A: GitHub connector 没给写权限,回 Routine 配置重新授权。

**Q: 改了持仓 commit 后,第二天分析是不是立刻用新持仓**
A: 是。Actions 读的就是 repo 里最新版本的 `positions.json`。

**Q: 我不想让 Routine 看到某个敏感的持仓**
A: 这架构做不到 "Actions 能看、Routine 看不到"。如需进一步隔离,请回退到方案 B 或自己实现加密方案。

---

## 安全提示

1. repo 保持 **Private**,不要误改成 Public
2. 给 GitHub 账号开 **2FA**(双因素认证)
3. PAT 放稳妥,不要 commit 到代码里
4. 定期检查 Actions 日志里没有泄漏持仓的输出(`--verbose` 模式下可能打印 positions json)
