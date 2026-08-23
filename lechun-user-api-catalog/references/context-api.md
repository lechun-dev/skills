# Context API

`lechun-user-api-catalog` 默认优先调用下面这个接口获取当前用户有权限查看的 API 上下文：

```text
GET /api/mcp/mall/apis/context
```

所有请求都必须携带请求头：

```text
X-Caller-Key: $LECHUN_API_KEY
```

示例：

```bash
curl -sS "https://scmapi.lechun.cc/lechun-mcp/api/mcp/mall/apis/context?keyword=sql&sourceSystem=lechun-mcp&limit=5" \
  -H "X-Caller-Key: $LECHUN_API_KEY"
```

支持参数：

- `keyword`：按 `apiCode`、`apiName`、`sourceSystem`、`path`、`owner`、`baseUrl` 模糊搜索
- `sourceSystem`：按来源系统精确过滤
- `apiCode`：按 API 编码精确过滤
- `limit`：返回条数，默认 `50`，最大 `200`

返回结构：

```json
{
  "userId": "3081774071860501911",
  "total": 2,
  "list": [
    {
      "apiCode": "mcp_sql_schemas",
      "apiName": "获取 SQL schema 列表",
      "sourceSystem": "lechun-mcp",
      "method": "GET",
      "path": "/api/sql/schemas",
      "fullPath": "lechun-mcp/api/sql/schemas",
      "baseUrl": "https://scmapi.lechun.cc/lechun-mcp",
      "protocol": "http",
      "owner": "wanghanxiao",
      "status": "enabled",
      "isPublic": true,
      "apiVersion": "v1",
      "timeoutMs": 3000,
      "requestSchema": "{}",
      "responseSchema": "{}",
      "updatedAt": "2026-04-07 13:00:00"
    }
  ]
}
```

使用策略：

1. 用户要“看全部 API”时，直接无筛选调用
2. 用户给了系统名时，优先传 `sourceSystem`
3. 用户给了关键词或业务目标时，先传 `keyword`
4. 命中结果较多时，先输出 3 到 5 个最相关 API，再附完整 JSON 上下文
5. 需要精确确认某个 API 时，再传 `apiCode`

降级策略：

若 `/api/mcp/mall/apis/context` 暂时不可用，退化为：

1. `GET /api/mcp/mall/apis`
2. 必要时再调用 `GET /api/mcp/mall/apis/{id}`
