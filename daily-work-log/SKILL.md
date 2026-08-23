---
name: daily-work-log
description: 在 LechunProjects 工作区内记录当日开发、分析、测试、部署、提交和推送动作，持续追加到 work-diary/YYYY-MM-DD.ndjson，并在下班或需要汇总时生成 Markdown 日报。适用于需要把 Codex 当天完成的工作结构化落盘、汇总成日报、并为后续上传到钉钉文档或钉钉表格做准备的场景。
---

# Daily Work Log

本 skill 用于把“今天干了什么”从对话中抽出来，写入本地结构化日志，再汇总成日报。

## 何时使用

- 完成代码修改后
- 完成测试、部署、提交、推送后
- 完成重要排障、方案设计、代码评审结论后
- 用户要求生成当日日报、阶段总结或同步到外部文档时

## 记录原则

- 只记录有效动作，不记录闲聊和中间探索噪音。
- 一次关键动作追加一条日志，不覆盖当天已有内容。
- `summary` 只写结果，不写铺垫。
- 优先记录：
  - 做了什么
  - 涉及哪个仓库/分支
  - 影响了哪些文件
  - 结果是成功、失败还是阻塞

## 日志文件

- 结构化日志：`work-diary/YYYY-MM-DD.ndjson`
- 汇总日报：`work-diary/YYYY-MM-DD.md`

## 使用方式

追加一条日志：

```bash
python3 skills/daily-work-log/scripts/append_daily_log.py \
  --repo /Users/wanghanxiao/LechunProjects/lechun-baseservice \
  --type code_change \
  --summary "为 Jenkins 部署 skill 增加 .env 自动加载" \
  --files skills/jenkins-job-trigger/scripts/env_utils.py \
  --files skills/jenkins-job-trigger/scripts/build_current_repo_test_job.py
```

生成今日日报：

```bash
python3 skills/daily-work-log/scripts/generate_daily_report.py
```

生成指定日期日报：

```bash
python3 skills/daily-work-log/scripts/generate_daily_report.py --date 2026-04-02
```

## 参数约定

`append_daily_log.py` 常用参数：

- `--summary`：必填，本次动作一句话总结
- `--type`：如 `code_change`、`analysis`、`test`、`deploy`、`commit`、`push`、`review`
- `--repo`：可选，默认当前目录；若在 Git 仓库内会自动识别仓库名与分支
- `--branch`：可选，未传时自动读取当前仓库分支
- `--files`：可重复传入，记录本次动作涉及文件
- `--result`：默认 `done`，失败可写 `failed`，阻塞可写 `blocked`
- `--details`：可选，补充短说明

## 输出要求

- 追加日志后，应在对用户的结论里自然说明“已记录到今日日志”。
- 生成日报后，应给出生成文件路径。
- 如果用户要求上传到钉钉文档或钉钉表格，再基于生成出的 Markdown 或结构化数据继续执行，不要跳过本地落盘。
