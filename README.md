# hoa-cli

这是给维护者用的命令行工具，不是给普通用户安装的应用。它从哈工大本部教务系统读取指定年级的执行教学计划，并更新 `hoa-major-data` 仓库中的两类文件：

- `major_mapping.json`：年级、学院和专业的目录；
- `plans/*.toml`：每个专业的课程列表。

它不会自动登录教务系统，也不会提交任何数据。正确的流程是：本地抓取 → 查看数据 diff → 人工确认后提交 `hoa-major-data`。

## 更新培养方案数据

抓取前需要两样本地信息：已登录教务系统会话的 Cookie，以及能访问教务系统的代理。二者都只保存在本机，绝不能提交。

1. 在 `hoa-cli` 仓库创建本地配置：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 编辑 `.env`：把浏览器中取得的 Cookie 填到 `HIT_JW_COOKIE`，代理地址填到 `HTTP_PROXY` 和 `HTTPS_PROXY`。其余值通常不用改。

3. 在 `hoa-cli` 仓库运行抓取，并明确指定要更新的数据仓库：

   ```powershell
   uv run hoa crawl --years 2024 2025 --data-dir ..\hoa-major-data
   ```

4. 审查生成的数据：

   ```powershell
   git -C ..\hoa-major-data diff -- major_mapping.json plans
   ```

`--years` 可以填写一个或多个年级。为保持原 HITSZ CLI 的行为，不写 `--data-dir` 时，工具会写入 `src/hoa_cli/data`。更新 `hoa-major-data` 时应像上例一样明确填写 `--data-dir ..\hoa-major-data`。

抓取只会替换指定年级的 `major_mapping.json` 条目和计划文件。它不会改动人工维护的 `lookup_table.toml`、`grades_summary.json`、`shared_categories.toml`。

## 修正专业名称或跳过记录

教务系统偶尔会漏掉班型名称，或返回不应发布的记录。这些例外写在 `src/hoa_cli/major_rules.toml`，不要写进 Python 代码。

每条规则必须同时写 `year` 和 `code`，所以只会影响对应年级的对应专业。例如：

```toml
[[rules]]
year = 2024
code = "09331"
name = "土木工程（土木菁华班）"
reason = "教务系统未在名称中显示班型"

[[rules]]
year = 2024
code = "01182L"
publish = false
reason = "与 01182 的方案重复，且来源没有独立名称"
```

- `year`：一个年级，或多个年级组成的列表；
- `code`：教务系统返回的专业代码；
- `name`：仅在需要覆盖教务系统名称时填写；
- `publish = false`：不生成这条记录；
- `reason`：写明判断依据，便于后续维护。

改完规则后，至少运行：

```powershell
uv run pytest tests/unit/test_discovery.py -q
uv run ruff check src tests
```

## 更新成绩构成

`grades_summary.json` 属于 `hoa-major-data`，不是 CLI 自己的数据。需要从 `repos-management` 的源文件重新生成它时，显式指定数据仓库：

```powershell
python scripts\update_grades_summary.py --data-dir ..\hoa-major-data
```

不写 `--data-dir` 时，脚本同样会按原 HITSZ CLI 的位置写入 `src/hoa_cli/data`。

## 开发验证

```powershell
make check
make build
```
