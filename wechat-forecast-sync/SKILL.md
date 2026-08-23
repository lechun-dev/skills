---
name: wechat-forecast-sync
description: 生成微信渠道供应链预测并产出 ERP 导入数据。适用于直接调用老 BI `sqlId=8` 查询历史销量，按乐纯既定规则生成微信低温未来 7 天预测 Excel 的场景。
---

# WeChat Forecast Sync

使用这个 skill 处理《接收微信运营供应链部分》里的“预测”开发项。

## Workflow

1. 当前脚本只实现“微信低温预测”。
2. 历史销量直接通过 `sqlId=8` 的 commonSql API 拉取，不再依赖本地 `history.json`。
3. 预测计算使用确定性脚本，不依赖大模型临场估值。
4. 脚本输出 Excel `sheet1`，字段固定为 `客户 / 物品 / 计划分类 / 仓库 / 提货日期 / 数量`。
5. ERP 真正导入前，先人工确认商品编码、计划分类和仓库映射。

## Commands

按当天规则计算未来 7 天低温预测并生成 Excel：

```bash
python3 skills/wechat-forecast-sync/scripts/run_wechat_forecast.py \
  --today 2026-04-28 \
  --output-xlsx /tmp/wechat-low-temp-forecast.xlsx \
  --output-json /tmp/wechat-low-temp-forecast.json
```

若 `LECHUN_API_KEY` 不在环境变量里，可显式传入：

```bash
python3 skills/wechat-forecast-sync/scripts/run_wechat_forecast.py \
  --today 2026-04-28 \
  --api-key "$LECHUN_API_KEY" \
  --output-xlsx /tmp/wechat-low-temp-forecast.xlsx
```

## Input Contracts

- `--today`
  - 锚点日期，脚本会从“明天”开始生成未来 `7` 天预测
- `--api-key`
  - `X-Caller-Key`，默认读取环境变量 `LECHUN_API_KEY`
- `--output-xlsx`
  - Excel 输出路径，必须提供
- `--output-json`
  - 可选调试输出，包含参考区间、商品日均等复核信息

## Fixed Rules

- 预测对象：微信低温
- 未来 `7` 天从“明天”开始
- 若未来 `7` 天命中 `6.18` 或 `11.11`：
  - 取去年同一大促所在自然周（周一到周日）
- 若未来 `7` 天未命中：
  - 取今天往前 `60` 天
  - 排除 `6.18` 和 `11.11` 所在自然周
  - 优先取后段；后段不存在或 `< 15` 天则取前段
- `sqlId=8` 查询参考区间商品总销量
- `日均销量 = 总销量 / 参考区间天数`
- 仓库按 `3:2:1` 分摊：
  - `00022` 京东北京仓
  - `00023` 京东上海仓
  - `00046` 松帆武汉低温仓
- 各仓数量分别向上取整
- Excel 表头：
  - `客户 / 物品 / 计划分类 / 仓库 / 提货日期 / 数量`
- `客户` 固定值：`KH00001`

详细规则和待补齐接口见 [references/forecast-rule-spec.md](./references/forecast-rule-spec.md)。

## Notes

- 当前版本已直连老 BI `sqlId=8`，但只实现低温预测。
- 常温一个月预测、大促上浮 5% 以外的复杂活动规则还没落脚本。
- Excel 采用脚本内置的最小 `.xlsx` 写法，不依赖第三方包。
