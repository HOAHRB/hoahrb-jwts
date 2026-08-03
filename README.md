# hoa-cli

`hoa-cli` 是一个维护者本地运行的哈尔滨工业大学（本部）本科执行教学计划采集器。它通过教务系统 HTTP 接口发现指定年级的院系、专业和课程，并生成 `hoa-major-data` 继续使用的 `major_mapping.json` 与 `plans/*.toml` 文件。

## 信任边界

采集需要维护者在本地通过代理访问教务系统，并手工提供一次性 Cookie。CLI 不自动登录、不保存用户名密码、不运行浏览器自动化；`.env` 已被忽略，Cookie 不得提交到仓库。公共 CI 只运行脱敏 fixture 测试和构建，不访问教务系统。

## 本地采集

```powershell
Copy-Item .env.example .env
# 在 .env 中填写 HIT_JW_COOKIE，并设置所需的本地代理。
uv run hoa crawl --years 2024 2025 --data-dir D:\dev\HOAHRB\hoa-major-data
git -C D:\dev\HOAHRB\hoa-major-data diff -- major_mapping.json plans
```

`--years` 和 `--data-dir` 都必须显式提供。采集完成后，维护者应审查新增、修改和删除的专业及课程，再提交 `hoa-major-data` 数据。采集器不会覆盖 `lookup_table.toml`、`grades_summary.json` 或 `shared_categories.toml`。

## 专业规则主文件

当教务系统漏掉专业方向、班型名称，或确认某条来源记录不应发布时，在 `src/hoa_cli/major_rules.toml` 增加一条规则。规则按 `year` 和 `code` 精确匹配，因此不会影响同一专业代码的其他年级。

- `year`：必填。可填写一个年级，例如 `2024`；同一规则需要用于多个年级时，写成列表，例如 `[2024, 2025]`。
- `code`：教务系统返回的专业代码。
- `publish`：是否生成该专业的数据；不填写时默认为生成，填 `false` 才会跳过。
- `name`：只有教务系统漏写或写错专业名称时才填写，用于替换来源名称。
- `reason`：说明作出这项人工修正的依据，方便日后核对。

修改规则后至少运行：

```powershell
uv run pytest tests/unit/test_discovery.py -q
uv run ruff check src tests
```

`grades_summary.json` 仍是 `hoa-major-data` 中人工维护的数据。需要从 `repos-management` 的源文件重建时，显式指定目标目录：

```powershell
python scripts/update_grades_summary.py --data-dir D:\dev\HOAHRB\hoa-major-data
```

## 开发验证

```powershell
make check
make build
```
