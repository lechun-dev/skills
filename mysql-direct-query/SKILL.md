---
name: mysql-direct-query
description: 通过根目录 .env 中的数据库连接信息直连 MySQL，区分线上库和测试库执行 schema、表结构和 SQL 查询。适用于不再使用 mysql-mcp、希望在执行前自动把 .env 加载为环境变量，并按 prod/test 明确隔离数据库访问的场景。脚本不依赖默认库，要求 SQL 显式使用库名.表名；线上库默认只允许只读 SQL，测试库允许执行查询、DML 和 DDL。
---

# MySQL Direct Query

使用这个 skill 直连 MySQL，不再经过 MCP。

## Workflow

1. 先确认工作区根目录 `.env` 已配置好 `MYSQL_PROD_*` 和 `MYSQL_TEST_*`。
2. 所有脚本执行前都会自动向上查找 `.env` 并加载到环境变量，不需要手工 `export`。
3. 查线上数据、表结构、schema 时使用 `--env prod`。
4. 改测试库数据、建表、调试 SQL 时使用 `--env test`。
5. 所有 `query` SQL 都必须显式写成 `库名.表名`，禁止依赖默认库。
6. 线上库默认只允许只读语句；测试库不做 SQL 类型限制。

## Environment Variables

根目录 `.env` 约定如下：

- `MYSQL_PROD_HOST`
- `MYSQL_PROD_PORT`
- `MYSQL_PROD_USER`
- `MYSQL_PROD_PASSWORD`
- `MYSQL_TEST_HOST`
- `MYSQL_TEST_PORT`
- `MYSQL_TEST_USER`
- `MYSQL_TEST_PASSWORD`

## Commands

查看线上可见 schema：

```bash
python3 skills/mysql-direct-query/scripts/mysql_cli.py schemas --env prod
```

查看测试库某个 schema 下的表：

```bash
python3 skills/mysql-direct-query/scripts/mysql_cli.py tables --env test --schema lechun_test
```

查看线上表结构：

```bash
python3 skills/mysql-direct-query/scripts/mysql_cli.py describe --env prod --table order_info
  --schema lechun_prod
```

执行线上只读 SQL：

```bash
python3 skills/mysql-direct-query/scripts/mysql_cli.py query \
  --env prod \
  --sql "SELECT id, order_no FROM lechun_prod.order_info ORDER BY id DESC LIMIT 20"
```

执行测试库更新 SQL：

```bash
python3 skills/mysql-direct-query/scripts/mysql_cli.py query \
  --env test \
  --sql "UPDATE lechun_test.demo_table SET status = 1 WHERE id = 1001"
```

从文件执行测试库 SQL：

```bash
python3 skills/mysql-direct-query/scripts/mysql_cli.py query \
  --env test \
  --file /absolute/path/to/debug.sql
```

## Notes

- `query` 不会执行 `USE 库名`，必须在 SQL 中显式写 `库名.表名`。
- `tables` 和 `describe` 必须显式传 `--schema`。
- 线上库禁止多语句执行，也禁止 `INSERT/UPDATE/DELETE/DDL` 等非只读 SQL。
- 测试库执行变更前，仍应先确认目标库和 SQL 内容，避免误连。
- 输出默认是 JSON，适合继续被 Codex 或脚本消费。
