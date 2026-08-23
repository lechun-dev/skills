---
name: eoq-calculator
description: 计算原料 EOQ 建议值并写入钉钉 AI 表格。适用于使用固定原料主数据接口拉取采购信息，按物料编码聚合、多供应商择优、通过 dws aitable 查重并新增或覆盖 EOQ 结果的场景。
---

# EOQ Calculator

使用这个 skill 处理乐纯原料 EOQ 建议值的批量计算和回写。

## Workflow

1. 优先使用 `scripts/run_eoq.py` 执行完整流程，不要手工逐条调用 `dws aitable`。
2. 脚本固定从原料主数据接口拉取采购信息，并按 `物料编码` 聚合供应商数据。
3. 默认模式下，脚本会先从 AI 表格加载已有 `物料编码`，已存在的物料直接跳过。
4. 传入 `--force` 时，脚本会重新计算并覆盖 AI 表格中的现有记录。
5. EOQ 计算使用确定性规则，不依赖大模型；说明字段也由脚本模板化生成。
6. 批量写入或更新 AI 表格时，脚本会自动按 100 条分批。

## Commands

全量计算，已存在的物料跳过：

```bash
python3 skills/eoq-calculator/scripts/run_eoq.py
```

只计算单个物料：

```bash
python3 skills/eoq-calculator/scripts/run_eoq.py --material-code 40100293
```

强制重算并覆盖现有记录：

```bash
python3 skills/eoq-calculator/scripts/run_eoq.py --material-code 40100293 --force
```

只预览结果，不实际写表：

```bash
python3 skills/eoq-calculator/scripts/run_eoq.py --material-code 40100293 --dry-run
```

调试时限制处理物料数：

```bash
python3 skills/eoq-calculator/scripts/run_eoq.py --limit-materials 20 --dry-run
```

## Fixed Config

脚本已内置以下固定配置：

- 原料主数据接口：`https://scmapi.lechun.cc/lechun-bi/commonSql/execute`
- 请求体：`{"sqlId":2,"dingId":"030966113929310965"}`
- AI 表格 Base：`G1DKw2zgV2RglZAbFBKRk9DlVB5r9YAn`
- AI 表格 Table：`hERWDMS`
- 查重字段：`物料编码`

如果接口要求额外鉴权，可通过环境变量提供：

- `LECHUN_API_KEY`：存在时，脚本会自动附带 `X-Caller-Key` 请求头

## Notes

- 本 skill 依赖本地已安装可用的 `dws` 命令。
- 记录写入时只使用 fieldId，不使用中文字段名直写。
- 多供应商择优规则默认按以下顺序比较：
  1. 推荐量对应阶梯的有效单价更低
  2. 推荐采购量更小
  3. 采购提前期更短
  4. 供应商名称字典序更小
- 对保质期 `<= 15` 天的物料，脚本会优先回落到较小批量采购，并在说明中标注短保覆盖逻辑。
- 详细计算规则见 [references/rule-spec.md](./references/rule-spec.md)。
