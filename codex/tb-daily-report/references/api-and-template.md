# TB Daily Report API And Template Reference

## Upstream APIs

Set `LECHUN_API_BASE` in your local environment for real usage. The public skill keeps only path examples and expects the real host to come from that environment variable.

Example:

```bash
export LECHUN_API_BASE="https://your-real-api-host.example.com"
```

### 1. Task activity report

- URL: `${LECHUN_API_BASE}/lechun-bi/tb/getTaskActivityReport`
- Method: `GET`
- Parameters:
  - `authUserId`: authenticated user identifier
  - `targetUserId`: target user identifier
  - `endTime`: 结束时间，取当前时间，格式 `yyyy-MM-dd HH:mm:ss`
- Expected usage:
  - fetch the latest 24-hour task changes
  - read collections such as `dependencies`, `myAssigned`, `assignedByMe`, `overdue`

### 2. Task report

- URL: `${LECHUN_API_BASE}/lechun-bi/tb/getTasksForReport`
- Method: `GET`
- Parameters:
  - `authUserId`: authenticated user identifier
  - `targetUserIds`: target user identifiers, comma-separated when multiple values are needed
  - `reportType`: `daily` for personal daily report, `weekly` for personal weekly report, `dept_weekly` for department weekly report
- Expected usage:
  - personal daily report must use `reportType=daily`
  - read `myTasks` for the task overview section

## Output Constraints

- Generate Markdown only.
- Keep every separator line `***`.
- Keep every table header even when there is no data.
- For empty tables, output one placeholder row and fill each cell with `-`.
- Use `[任务名称](任务链接)` for tasks.
- Use `[项目名称](项目链接)` for projects.
- For multi-task cells, render each entry as `➣ [任务名称](任务链接) / 状态 / 执行人 / DDL；`
- Do not invent rows, counts, work hours, or status changes.
- `overdue` is especially strict: if empty, do not synthesize delayed tasks.

## Exact Report Template

Replace `统计对象` with the employee name. Replace `${Script_a0JJ.msg_date}` with the report date when available.

Keep any real endpoint hostnames, auth scheme details, and organization-specific identifier names in private deployment docs or environment configuration rather than this public reference.

```md
#  📕 「统计对象」日报（${Script_a0JJ.msg_date}）

***

## 一、今天你必须做什么 + 你被谁卡住、你卡住了谁（依赖与协作的最短闭环）

***

| 任务 | 项目 | 截止日期 | 依赖任务 / 状态 / 执行人 / DDL | 阻塞任务 / 状态 / 执行人 / DDL|
|------|------|----------|--------------------------------|--------------------------------|
| [任务名称](任务链接) | [项目名称](项目链接)  |  |   |   |

***

## 二、过去 24h 发生了哪些值得我关注的变化（变更驱动同步）

### (1) 哪些我依赖的关键任务，过去 24 小时出现了截止变更

***

| 我依赖任务 | 项目 | 截止日期变化 | 更新日期 | 执行人 |
|------------|------|--------------|----------|--------|
| [任务名称](任务链接) | [项目名称](项目链接)  |  |  |  |

***

### (2) 过去 24 小时指派给我的任务，出现新增或者变化

***

| 我接收的任务 | 项目 | 截止日期/状态的变化或新增 | 依赖或阻塞的任务和对象 |
|--------------|------|---------------------|-------------------------------|
| [任务名称](任务链接) |   |   |   |

***

### (3) 过去 24 小时我指派的任务，出现状态变化 / 截止日期 / 任务信息变化

***

| 我指派的任务 | 项目 | 截止日期的变化或新增 | 任务状态变化 | 任务信息更新 | 责任人 |
|--------------|------|---------------------|--------------|--------------|--------|
| [任务名称](任务链接) |   |   |   |   |   |

***

### (4) 过去 24 小时我负责的关键任务延期

***

| 数字编号 | 我的关键任务 | 项目 | 截止日期 | 阻塞任务 / 状态 / 执行人 / DDL |
|----------|--------------|------|----------|---------------------------|
| 数字索引 | [任务名称](任务链接) |   |   |   |
```
