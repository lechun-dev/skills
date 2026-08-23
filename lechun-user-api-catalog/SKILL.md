---
name: lechun-user-api-catalog
description: 获取当前用户有权限使用的乐纯内部 API，支持按系统、关键词和业务目标筛选，并输出适合 Codex 继续创建 Skill、脚本或工作流的结构化 API 上下文。
---

# lechun-user-api-catalog

当用户需要查看“我当前能用哪些 API”、筛选适合某个业务目标的 API、或让 Codex 基于当前用户可用 API 继续编写 Skill、脚本、工作流时，使用本 Skill。

## 目标

把当前用户有权限使用的乐纯内部 API，整理成：

- 人可读的能力摘要
- Codex 可继续消费的结构化上下文

不要自己重做权限判断。只消费后端已经按当前用户过滤后的 API 目录。

## 核心规则

- 所有对乐纯 HTTP 接口的调用都必须携带请求头 `X-Caller-Key=$LECHUN_API_KEY`。
- `LECHUN_API_KEY` 来自业务人员本地环境变量，不要在 Skill 中硬编码真实 key。
- 如果当前环境没有 `LECHUN_API_KEY`，可以继续输出分析和建议，但不要伪造接口调用结果。
- 对用户展示结果时，不要回显真实 API Key 或完整请求头。

## 适用场景

- 列出当前用户可见的全部 API
- 按来源系统筛选 API
- 按关键词筛选 API
- 按业务目标推荐 API
- 为选中的 API 生成后续 Skill 开发可用的调用素材

## 工作流

1. 优先获取当前用户 API 目录。
2. 判断用户意图是全量列举、按条件筛选，还是按业务目标推荐。
3. 输出摘要列表。
4. 如用户后续要创建 Skill、脚本或工作流，再输出结构化上下文和调用素材。

## 数据来源

优先使用统一接口：

```text
GET /api/mcp/mall/apis/context
```

接口参数、返回结构和调用策略，详见 [references/context-api.md](references/context-api.md)。

如果该接口尚未实现，退化为：

1. `GET /api/mcp/mall/apis`
2. 必要时按 `id` 调 `GET /api/mcp/mall/apis/{id}`

## 默认输出格式

默认输出两段：

### 1. 摘要

至少包含：

- API 名称
- API 编码
- 来源系统
- 请求方式
- 路径
- 用途说明

### 2. 结构化上下文

使用 JSON，字段优先包含：

- `apiCode`
- `apiName`
- `sourceSystem`
- `method`
- `path`
- `fullPath`
- `baseUrl`
- `protocol`
- `owner`
- `apiVersion`
- `timeoutMs`
- `requestSchema`
- `responseSchema`
- `isPublic`
- `matchReason`
- `recommendedUsage`

## 推荐规则

如果用户给的是业务目标，优先按以下字段匹配：

1. `apiName`
2. `apiCode`
3. `path`
4. `sourceSystem`
5. `description`

默认优先推荐：

- 查询类 API
- Schema 完整的 API
- 命名清晰的 API

默认弱化推荐：

- 写操作 API
- Schema 缺失的 API
- 无法从名称或路径判断用途的 API

## 后续动作建议

当用户继续说：

- “帮我基于这些 API 写一个 Skill”
- “给我 curl 示例”
- “给我 JS/Python 调用代码”
- “帮我组合成 workflow”

直接基于本 Skill 输出的结构化上下文继续生成，不要再回到页面式描述。

## 注意事项

- 不要猜测用户无权限的 API。
- 不要绕过当前用户权限链。
- 若没有匹配结果，要明确说明“当前用户暂无匹配 API”。
- 若用户目标过宽，优先给出 3 到 5 个推荐 API，不要直接全量倾倒。
