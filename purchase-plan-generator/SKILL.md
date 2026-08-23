---
name: purchase-plan-generator
description: 生成原料周采购计划并写入钉钉 AI 表格。适用于使用固定毛需求/原料主数据/库存/在途接口，按物料编码和仓库聚合，基于 EOQ 结果执行 MRP 滚动计算，按周创建或复用采购计划表，并通过 dws 写入计划记录、输出报告和发送通知的场景。
---

# Purchase Plan Generator

使用这个 skill 处理乐纯原料周采购计划的生成、回写和通知。

## Workflow

1. 优先使用 `scripts/run_purchase_plan.py` 执行完整流程，不要手工逐条调用 `dws aitable`。
2. 脚本固定拉取毛需求、原料主数据、库存、在途和 EOQ 结果。
3. 计划表按周生成，表名固定为 `采购计划-<本周一>`；存在则复用，不存在则自动创建。
4. 默认模式下，脚本会先加载当前周表中已有的 `(物料编码, 仓库)`，已存在组合直接跳过。
5. 采购计划计算使用确定性脚本，不依赖大模型；报告由脚本直接输出 markdown。
6. 批量写入 AI 表格时，脚本会自动按 30 组物料仓库分批，对应每批最多 90 行记录。
7. 默认会通过固定 RobotCode 发送一条 Markdown 机器人单聊消息给王汉晓；若不需要通知，显式传 `--no-notify`。

## Commands

全量生成本周采购计划，已存在的物料仓库组合跳过：

```bash
python3 skills/purchase-plan-generator/scripts/run_purchase_plan.py
```

只计算单个物料：

```bash
python3 skills/purchase-plan-generator/scripts/run_purchase_plan.py --material-code 40100870
```

只计算单个仓库：

```bash
python3 skills/purchase-plan-generator/scripts/run_purchase_plan.py --warehouse 东君物料仓
```

强制重算并覆盖当前周已有记录：

```bash
python3 skills/purchase-plan-generator/scripts/run_purchase_plan.py --material-code 40100870 --warehouse 东君物料仓 --force
```

只预览结果，不建表不写表不发通知：

```bash
python3 skills/purchase-plan-generator/scripts/run_purchase_plan.py --dry-run --limit-items 5
```

生成报告文件：

```bash
python3 skills/purchase-plan-generator/scripts/run_purchase_plan.py --report-file /tmp/purchase-plan-report.md
```

## Fixed Config

脚本已内置以下固定配置：

- SCM 接口：`https://scmapi.lechun.cc/lechun-bi/commonSql/execute`
- 毛需求：`{"sqlId":7,"dingId":"030966113929310965"}`
- 原料主数据：`{"sqlId":2,"dingId":"030966113929310965"}`
- 库存：`{"sqlId":4,"dingId":"030966113929310965","params":{"matCode":"<物料编码>"}}`
- 在途：`{"sqlId":5,"dingId":"030966113929310965","params":{"matCode":"<物料编码>"}}`
- 采购计划 AI 表格 Base：`a9E05BDRVQ6rpEjdt2YK16RKJ63zgkYA`
- EOQ AI 表格 Base：`G1DKw2zgV2RglZAbFBKRk9DlVB5r9YAn`
- EOQ AI 表格 Table：`hERWDMS`
- 默认通知接收人：`030966113929310965`
- 默认机器人 RobotCode：`dingvdcze6qot9oisdgl`

如果接口要求额外鉴权，可通过环境变量提供：

- `LECHUN_API_KEY`：存在时，脚本会自动附带 `X-Caller-Key` 请求头

## Notes

- 本 skill 依赖本地已安装可用的 `dws` 命令。
- 当前周表结构固定为：`序号 / 物料编码 / 物料名称 / 类型 / 13 个周列 / 仓库`。
- 当前表内每个 `(物料编码, 仓库)` 固定写入 3 行：`需求数量 / 库存数量 / 采购数量`。
- 默认通知方式是 `dws chat message send-by-bot`，消息体直接使用 markdown 报告全文。
- EOQ 缺失、提前期非法、周表结构异常都会被记入风险报告，不会静默猜值。
- 详细规则见 [references/planning-rule-spec.md](./references/planning-rule-spec.md)。
