---
name: jenkins-job-trigger
description: 通过 Jenkins HTTP API 查询 Job 参数并触发带参数的构建。适用于已知 Jenkins 地址、使用账号密码登录、需要选择 branch 或其他参数后触发某个 Job 的场景。优先使用本 skill 自带脚本处理 Basic Auth、CSRF crumb、参数查询和构建触发。
---

# Jenkins Job Trigger

使用这个 skill 处理 Jenkins Job 的参数查询、构建触发和基础状态确认。

## Workflow

1. 优先使用 `scripts/trigger_jenkins_job.py params ...` 查询 Job 的参数定义。
2. 确认参数名和值后，使用 `scripts/trigger_jenkins_job.py build ...` 触发构建。
3. Jenkins 开启 CSRF 时，脚本会自动先获取 crumb。
4. 如果只知道页面上显示需要选择 `branch`，但不确定真实参数名，先查参数再触发，不要猜。
5. 如果当前目录是 Git 仓库，且仓库与 Jenkins 测试环境 job 存在固定映射，优先使用 `scripts/build_current_repo_test_job.py` 自动读取当前分支并触发对应测试 job。
6. 触发完成后，优先使用 `scripts/watch_jenkins_build.py` 跟踪构建结果。
7. 构建结束后，使用 `scripts/analyze_jenkins_build.py` 读取 Jenkins job 日志。成功与否只以 Jenkins 构建状态为准；如果失败或异常，输出关键报错摘要。

## Environment Variables

脚本默认从环境变量读取 Jenkins 连接信息：

- `JENKINS_URL`
- `JENKINS_USER`
- `JENKINS_PASSWORD`

也可以通过命令行参数显式传入。

## Commands

查询 `lechun_test_bi` 的参数定义：

```bash
python3 scripts/trigger_jenkins_job.py params \
  --url 'http://59.110.6.9:8077/' \
  --job 'lechun_test_bi' \
  --user "$JENKINS_USER" \
  --password "$JENKINS_PASSWORD"
```

带参数触发构建：

```bash
python3 scripts/trigger_jenkins_job.py build \
  --url 'http://59.110.6.9:8077/' \
  --job 'lechun_test_bi' \
  --user "$JENKINS_USER" \
  --password "$JENKINS_PASSWORD" \
  --param branch=master
```

如果已导出环境变量，可以省略连接参数：

```bash
export JENKINS_URL='http://59.110.6.9:8077/'
export JENKINS_USER='your_user'
export JENKINS_PASSWORD='your_password'

python3 scripts/trigger_jenkins_job.py params --job 'lechun_test_bi'
python3 scripts/trigger_jenkins_job.py build --job 'lechun_test_bi' --param branch=master
```

按当前仓库自动推导 Jenkins job 并触发：

```bash
python3 scripts/build_current_repo_test_job.py show --env test --branch test
python3 scripts/build_current_repo_test_job.py build --env test --branch test
python3 scripts/build_current_repo_test_job.py build-watch --env test --branch test
python3 scripts/build_current_repo_test_job.py build-watch --env product --branch master
```

脚本不会默认读取当前 Git 分支。必须显式传入 `--branch`，然后自动拼成 Jenkins 的 `branch` 参数，例如 `test` -> `origin/test`。

从队列项追踪到构建结果：

```bash
python3 scripts/watch_jenkins_build.py \
  --queue-url 'http://59.110.6.9:8077/queue/item/25096/'
```

如果当前 Jenkins 会很快清理队列项，优先直接按 job 和参数跟踪：

```bash
python3 scripts/watch_jenkins_build.py \
  --job 'lechun_test_bi' \
  --match-param branch=origin/test
```

分析某次构建日志并提取结论：

```bash
python3 scripts/analyze_jenkins_build.py \
  --job 'lechun_test_bi' \
  --build-number 388
```

## Notes

- Jenkins Job 页面要求选择 branch，通常意味着该 Job 是参数化构建，应调用 `buildWithParameters`。
- 某些 Jenkins 会返回 201 或 302，这通常表示已进入队列，不是失败。
- 如果接口返回 403，优先检查账号权限、密码、CSRF crumb 和反向代理限制。
- 当前仓库到测试 job 的映射维护在 [references/job_map.json](./references/job_map.json)。
- 对这台 Jenkins，更推荐 `build-watch` 或 `watch_jenkins_build.py --job ... --match-param ...`，因为队列项会很快 404。
- 这个 skill 的定位是 Jenkins 部署，不负责页面功能验证。最终结论只以 Jenkins 构建状态为准；job 日志只用于失败时提取关键报错片段。
- 为避免误用，`build_current_repo_test_job.py` 触发部署时必须显式传入 `--branch`。不允许默认使用当前本地分支。
