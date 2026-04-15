# bei-x — @bei_zhang01 的 X 自动化运营 Bot

> 用 AI + 浏览器自动化，让 @bei_zhang01 在 Deep Research AI 社区保持高活跃度——每天自动转发团队内容、精选行业动态、生成高质量评论、增长目标粉丝，为 MiroMind 做持续精准曝光。

---

## 这个项目做什么

Bei Zhang 是 [MiroMind](https://miromind.ai) 的 Growth VP。MiroMind 是一家做企业级 AI 的公司，核心产品是 **MiroThinker 235B** 大模型 + **MiroMind OS**，方向是从概率生成转向可验证的逐步推理（verifiable step-by-step reasoning）。

这个 repo 是 `@bei_zhang01` X 账号的自动化运营系统，目标是在 Deep Research AI 社区建立影响力。

**Fork 自** [mguozhen/social-bot](https://github.com/mguozhen/social-bot)，在其基础上做了专项扩展。

---

## 四个每日流程

### Flow 1 — 团队转发（全自动）

监控 12 个团队账号的时间线：

`@miromind_ai` `@tianqiao_chen` `@LidongBing` `@KaiyuYang4` `@SimonShaoleiDu` `@BinWang_Eng` `@Yifan_Zhang77` `@JeremyFeng98` `@XingxuanLi` `@Yang_zy223` `@christlurker` `@howtocluster`

AI 判断每条推文是否与 AI 研究/技术相关 → 相关就转发，无关就跳过。

### Flow 2 — Deep Research 内容精选（全自动）

搜索关键词：`Deep Research AI`、`#DeepResearch`、`deep reasoning AI`、`AI reasoning verification`、`system 2 AI`、`verifiable AI reasoning`

AI 评估内容质量和互动价值 → 高质量内容自动转发，标记可互动的帖子给 Flow 3。

### Flow 3 — 社区互动评论（半自动，需人工审核）

对 Flow 2 筛出的可互动帖子，AI 以 Bei Zhang 的风格生成评论，进入审核队列。

**评论不会自动发出**——需要在 Dashboard 中逐条审批或拒绝。

AI 人设：有洞察力、技术可信、绝不推销。只在自然相关时提及 MiroMind。

### Flow 4 — 智能关注（全自动，每天上限 15 个）

首次运行时导入 `follow_list.txt`（约 2000 个目标账号）到数据库。每天从队列中取 15 个执行关注。Flow 2/3 过程中发现的高影响力账号也会自动入队。

---

## 系统架构

```
config.json（账号/关键词/人设配置）
       ↓
run_daily.py（调度 4 个 Flow，macOS LaunchAgent 每天 10:05 触发）
       ↓
bot/x_bot.py（4 个核心函数，调用 browser + AI）
       ↓
bot/browser.py（browse CLI 控制本地 Chrome，无需 API Key）
       ↓
bot/ai_engine.py（OpenRouter → Claude Haiku 过滤 + Claude Sonnet 生成）
       ↓
bot/db.py（SQLite：回复记录 / 审核队列 / 关注队列）
       ↓
dashboard/app.py（Flask Web 面板，端口 5050）
```

---

## 快速开始

### 前置条件

- macOS + Python 3
- [browse CLI](https://github.com/anthropics/browse) 已安装
- Chrome 浏览器已登录 `@bei_zhang01`

### 安装

```bash
git clone https://github.com/beizhangnina/shanda-x-bei.git
cd shanda-x-bei
pip install -r requirements.txt
```

### 配置

创建 `.env` 文件：

```
OPENROUTER_API_KEY=你的key
```

编辑 `config.json` 自定义账号、关键词、人设。

### 运行

```bash
# 运行全部 4 个 Flow
python run_daily.py

# 运行单个 Flow（调试用）
python run_daily.py --flow 1    # 团队转发
python run_daily.py --flow 2    # DR 搜索转发
python run_daily.py --flow 3    # 评论队列
python run_daily.py --flow 4    # 关注批次

# 跳过启动随机延时（测试用）
python run_daily.py --no-jitter

# 跳过 Flow 4（今天已达关注上限时）
python run_daily.py --skip-follow
```

### Dashboard

```bash
python dashboard/app.py
# 打开 http://localhost:5050
```

Dashboard 提供：
- 今日运行数据概览（转发数、评论数、关注数）
- 评论审核面板（逐条审批/拒绝 AI 生成的评论）

---

## 项目结构

```
shanda-x-bei/
├── bot/
│   ├── x_bot.py            # 4 个核心 Flow 函数
│   ├── ai_engine.py        # OpenRouter 调用（Haiku 过滤 + Sonnet 生成）
│   ├── browser.py          # browse CLI 封装
│   ├── db.py               # SQLite 数据层
│   └── __init__.py
├── dashboard/
│   ├── app.py              # Flask Web 面板
│   └── templates/
├── docs/
│   └── superpowers/specs/  # 设计文档
├── launchd/                # macOS LaunchAgent 配置
├── config.json             # 账号 / 关键词 / 人设配置
├── follow_list.txt         # ~2000 个待关注账号
├── run_daily.py            # 主入口 + 调度器
├── install.sh              # 一键安装脚本
└── requirements.txt
```

---

## 速率控制与安全

| 参数 | 值 |
|------|-----|
| 每日最大转发 | 15 条 |
| 每日最大评论 | 10 条 |
| 每日最大关注 | 30 个 |
| 操作间隔 | ≥ 5 分钟 |
| 启动随机延时 | 0–20 分钟 |
| 评论发布 | 需人工审核 |
| 去重 | SQLite 记录，同一帖子不重复操作 |

---

## 技术栈

- **Python 3** — 主语言
- **browse CLI** — 浏览器自动化，无需 X API Key
- **OpenRouter** — AI 调用（Claude Haiku 做过滤，Claude Sonnet 做内容生成）
- **SQLite** — 本地数据存储
- **Flask** — Dashboard Web 面板
- **macOS LaunchAgent** — 每日定时调度
