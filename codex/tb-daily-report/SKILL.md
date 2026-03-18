---
name: tb-daily-report
description: Generate a personal TeamBition daily report in strict Markdown from TeamBition task data and 24-hour activity data. Use when Codex needs to汇总员工个人日报、解析 TeamBition 任务与动态接口、按固定日报模板输出、严格保留空表头和占位符、或避免编造日报内容。
---

# TB Daily Report

## Overview

Generate a personal TeamBition daily report from two upstream HTTP APIs and render it in one exact Markdown template. Preserve source fidelity: do not invent tasks, projects, links, dates, dependencies, blockers, or totals that are not present in the provided data.

## Workflow

1. Read the request and identify:
   - the employee being reported
   - the login DingTalk user
   - the target `dingIds` list for task-report requests
   - the target `dingId` value for activity-report requests
   - the reporting date
2. Fetch both upstream data sources described in [references/api-and-template.md](references/api-and-template.md).
3. Inspect the returned JSON and locate the required collections by name:
   - task overview: `myTasks`
   - 24h changes: `dependencies`, `myAssigned`, `assignedByMe`, `overdue`
4. Render the report in the exact template from the reference file.
5. Validate every table:
   - keep the header even when the data array is empty
   - output one placeholder row with `-` in each empty cell when the table is empty
   - ensure every markdown table row ends with a newline

## Hard Rules

- Output only the final Markdown report unless the user explicitly asks for analysis or raw data.
- Follow the template literally. Keep every `***` separator.
- Use only the specified source collections for each section.
- Show all rows from `myTasks`. Do not filter, sort away, deduplicate, or add derived tasks unless the user explicitly overrides that rule.
- Treat `overdue` as the only source for section `(4)`. If it is empty, keep only the header plus one `-` placeholder row.
- If a field is missing or blank, render `-`.
- Replace every task mention with `[任务名称](任务链接)`.
- Replace every project mention with `[项目名称](项目链接)`.
- When a table cell contains multiple task entries, format each entry as `➣ [任务名称](任务链接) / 状态 / 执行人 / DDL；`
- Do not claim a status change, deadline change, dependency, blocker, or work-hour total unless it is supported by the payload.

## Rendering Rules

- Use the employee name in the title if the request provides it. If the request passes template variables such as `${Start_qaKR.sender_nick}` or `${Script_a0JJ.msg_date}`, substitute them with the resolved values before producing the final Markdown when those values are available.
- In section `(3)`, fill:
  - `截止日期的变化或新增` from deadline-related changes
  - `任务状态变化` from status-related changes
  - `任务信息更新` by joining deadline/status/info changes with `、` when the source distinguishes them
- For dependency or blocker columns, render linked task summaries in the format `➣ [任务名称](任务链接) / 状态 / 执行人 / DDL；`
- For numeric index columns, emit ascending integers starting from `1`. If the table is empty, use `-`.
- If the upstream payload contains work-hour data and the user asks for work-hour totals, calculate carefully and surface only arithmetic directly supported by the data.

## Data Mapping Discipline

- Prefer direct field mapping from the payload over inference.
- If the payload shape differs from the examples, inspect the returned keys and map semantically equivalent fields without changing the section-to-source contract.
- If a needed value cannot be found reliably, render `-` instead of guessing.
- If the APIs fail or return incomplete data, state that briefly only if the user asked for diagnostics; otherwise stop and ask for a retry or the missing inputs.

## References

- Read [references/api-and-template.md](references/api-and-template.md) for the endpoint definitions, parameter rules, and the exact output template.
