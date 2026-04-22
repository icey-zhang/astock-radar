# 配置指南 · Routine + Project 组合方案

这份文档解决一个具体问题:**让这个 routine 每天自动跑起来,结果能直接在 Claude.ai 里深度分析。**

## 架构一张图

```
  Claude Code Routine (云端, 每交易日 15:30)
  ├─ clone/pull GitHub repo
  ├─ bash scripts/run_daily.sh --with-news
  └─ git commit & push reports/ 和 data/
                          │
                          ▼
           GitHub private repo (中转站)
                          │
                          ▼
  Claude.ai Project (你的"AI分析员")
  ├─ Project Knowledge: SKILL.md / analyst_prompt.md / stocks.yml
  ├─ Custom Instructions: docs/project_instructions.md 的内容
  └─ 你贴入 signals.json → Claude 做 web_search + 综合 → artifact
```

---

## 一次性配置(~15 分钟)

### Step 1 · 把项目放到 GitHub private repo

```bash
# 在你本地(Mac)
cd /path/to/astock_daily_analysis

# 改成你自己的 watchlist 和持仓(重要!)
vim config/stocks.yml        # 替换 watchlist 股票代码
vim config/positions.json    # 填真实持仓(或清空)

# 初始化 git
git init
git add .
git commit -m "init: A股每日分析 routine"

# 在 GitHub 网页上创建 private repo(建议 private,别暴露持仓)
# 然后 push
git remote add origin git@github.com:YOUR_USERNAME/astock_daily_analysis.git
git branch -M main
git push -u origin main
```

> ⚠ **务必用 private repo**。`config/positions.json` 里有你的持仓成本,push 到 public 就泄露了。

### Step 2 · 配置 Claude Code Routine

1. **打开 Routines 管理页**:[claude.ai/code/routines](https://claude.ai/code/routines)(需要 Claude Code 订阅;Pro 每天 5 次额度,日常足够)

2. **点 "New routine" → 选 "Scheduled"**

3. **填写触发条件**:
   - Schedule: **Custom cron** → `30 7 * * 1-5`(UTC 07:30 = 北京 15:30,工作日)
   - 如果偏好图形化,选 "Weekdays" + 时间 `7:30 AM UTC`
   - 为什么不直接选北京时间?Routines 的 cron 目前用 UTC,手动转一下最稳

4. **粘贴 prompt**:
   打开 [`docs/routine_prompt.md`](routine_prompt.md),把**整个文件的内容**复制到 Routine 的 Prompt 输入框。里面已经替你写好了 clone → 跑 → commit 的完整指令。**但需要替换一处**:把 `https://github.com/YOUR_USERNAME/astock_daily_analysis.git` 改成你自己的 repo URL。

5. **配置 Environment**:
   - **Network access**: 选 "Custom allowlist",加入以下域名(akshare 需要):
     ```
     push2.eastmoney.com
     push2his.eastmoney.com
     datacenter-web.eastmoney.com
     hq.sinajs.cn
     money.finance.sina.com.cn
     quotes.sina.cn
     api.github.com
     github.com
     pypi.org
     files.pythonhosted.org
     ```
   - **Setup script**(可选,加速冷启动):
     ```bash
     pip install -r requirements.txt --quiet --break-system-packages
     ```
   - **Environment variables**:不需要(没用到 API key)

6. **GitHub connector**:
   - 在 Routine 设置里连接 GitHub,授权**只**访问你刚创建的 `astock_daily_analysis` repo
   - 这样 Routine 才能 git push

7. **先手动跑一次**:点 "Run now",看 session 日志。确认:
   - `run_daily.sh` 成功执行
   - `reports/YYYY-MM-DD.md` 和 `data/YYYY-MM-DD/signals.json` 被 commit
   - GitHub 上能看到新 commit

**常见 Routine 故障排查**:
- 网络超时 → 检查 allowlist 里的域名,akshare 偶尔会用新域名,去 `scripts/fetch_data.py` 看实际报错日志
- akshare 接口 401/403 → 国内数据源有时会临时封 Anthropic 云 IP,fallback 方案是切换到本地 Mac cron(见附录 A)
- git push 失败 → GitHub connector 权限不够,重新授权

### Step 3 · 配置 Claude.ai Project

1. **打开 Claude.ai**,点侧边栏 "Projects" → "Create Project"

2. **命名**:`A股每日分析`,描述随便填

3. **Custom Instructions**:打开 [`docs/project_instructions.md`](project_instructions.md),复制**整个文件的内容**粘贴进去。这是给 Project 的"系统提示词",每次新对话 Claude 都会读。

4. **Project Knowledge**(上传以下文件):
   - `SKILL.md`
   - `prompts/analyst_prompt.md`
   - `config/stocks.yml`(这样 Claude 知道你在关注哪些股票)
   - `README.md`(可选,给 Claude 上下文)
   
   **不要**上传 `config/positions.json`(持仓数据每日有变,且敏感),用时再粘贴到对话。

5. **测试**:在这个 Project 里开新对话,说:
   > "今天的 signals.json 还没生成,先帮我解释一下你的工作流"
   
   Claude 应该根据 Custom Instructions 描述它会做什么。

---

## 每日使用流程(配置完后)

### 北京时间 15:30 前后
- Routine 在云端自动触发,~2-3 分钟内完成
- 你会在 Claude Code 的 Routines 页看到 "completed" 状态
- 对应日期的 commit 出现在 GitHub repo

### 你想看分析时(任意时间)
1. 打开 GitHub repo 的 `reports/` 目录,找到当日 `YYYY-MM-DD_dashboard.html`
2. 下载到本地,浏览器打开 → 先看仪表盘里的 K 线、信号、统计卡
3. 如果想让 Claude 深度分析,**把 `data/YYYY-MM-DD/signals.json` 的内容复制**
4. 进 Claude.ai 的 "A股每日分析" Project,开新对话,粘贴 JSON + 说"做今日分析"
5. Claude 按 SKILL + analyst_prompt 的规则:web_search 补消息面 → 综合判断 → 输出完整 Markdown 报告(可选再要一份填入 AI 的 HTML)

### 更省事的做法
让 Routine 也把 `reports/YYYY-MM-DD.md` 内容作为 Routine 的**最终输出**(已写在 prompt 里)。这样你在 Claude Code 的 Routine session 记录里就能直接看到骨架报告,不用再去 GitHub 查。

---

## 附录 A · Fallback:Mac 本地 cron

如果 Routine 的 akshare 出站访问长期不稳定,退而用 Mac 本地 cron。每天 15:30 跑,当天 Mac 没关机都能触发:

```bash
# 编辑 crontab
crontab -e

# 加一行(Python 路径替换成你的)
30 15 * * 1-5 cd /path/to/astock_daily_analysis && /usr/local/bin/python3 -m pip install -r requirements.txt -q && bash scripts/run_daily.sh --with-news >> /tmp/astock_daily.log 2>&1

# 保存退出
```

然后手动 `git push` 到 repo,或者让 cron 也做 git push。区别只是"从哪里发起"。

## 附录 B · Fallback:GitHub Actions

如果 Routine 的云端和 akshare 真的无法连通,用 GitHub Actions 作为纯定时方案。workflow 文件我已经放在 [`.github/workflows/daily.yml`](../.github/workflows/daily.yml),push 到 repo 后自动生效。区别是 GitHub Actions 的 runner 在美国,akshare 对国外 IP 可能限流更严,不如 Claude Code Routines 或本地 cron 稳。

## 附录 C · Routine 如何知道今天是交易日

`scripts/trading_day_check.py` 用 akshare 的 `tool_trade_date_hist_sina()` 判断。即使 cron 配了 1-5(周一到周五),春节、国庆等假期也会被这个脚本过滤。Routine 遇到非交易日就提前 exit,不消耗额度。
