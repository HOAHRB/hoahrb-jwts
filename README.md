# hoahrb-jwts

这是给维护者用的命令行工具，不是给普通用户安装的应用。它从哈工大本部教务系统读取指定年级的执行教学计划和公开成绩构成，并更新 `hoa-major-data` 仓库中的数据文件：

> **与原 HITSZ 教务工具的关键区别：本项目已重构为无状态的培养方案采集器，只负责抓取并输出数据；原有的查询等便捷功能不再提供。**

- `major_mapping.json`：年级、学院和专业的目录；
- `plans/*.toml`：每个专业的课程列表。
- `grades_summary.json`：课程成绩构成及其年级变体。
- `course_introductions.json`：培养方案中课程的中英文简介。

它不会自动登录教务系统，也不会提交任何数据。正确的流程是：本地抓取 → 查看数据 diff → 人工确认后提交 `hoa-major-data`。

## Usage

### 准备环境

需要 Python 3.14 或更高版本以及 [uv](https://docs.astral.sh/uv/)。克隆仓库后安装项目和开发依赖：

```powershell
uv sync --dev
```

在 `hoahrb-jwts` 仓库创建本地配置：

```powershell
Copy-Item .env.example .env
```

先在浏览器中登录哈工大本部教务系统，再编辑 `.env`：把当前会话的完整 Cookie 填入 `HIT_JW_COOKIE`。只有通过代理才能访问教务系统时，才需要填写 `HTTP_PROXY` 和 `HTTPS_PROXY`。Cookie 和代理配置只应保存在本机，绝不能提交。

常用配置如下：

| 变量 | 必需 | 说明 |
| --- | --- | --- |
| `HIT_JW_COOKIE` | 是 | 已登录教务系统会话的完整 Cookie |
| `HIT_JW_BASE_URL` | 否 | 教务系统地址，默认 `http://jwts.hit.edu.cn` |
| `HTTP_PROXY` / `HTTPS_PROXY` | 否 | 访问教务系统所需的代理 |
| `HIT_JW_TIMEOUT_SECONDS` | 否 | 单次请求超时，默认 20 秒 |
| `HIT_JW_DELAY_SECONDS` | 否 | 连续请求间隔，默认 0.2 秒 |
| `HIT_JW_MAX_RETRIES` | 否 | 可重试请求次数，默认 3 次 |

### 命令概览

```powershell
uv run jwts --help
uv run jwts --version
```

| 命令 | 用途 |
| --- | --- |
| `uv run jwts crawl --years 2024 2025` | 抓取一个或多个年级的培养方案 |
| `uv run jwts grades` | 抓取当前账号成绩记录中可见的成绩构成 |
| `uv run jwts crawl-grades` | `grades` 的兼容别名 |

两个抓取命令都支持以下选项：

| 选项 | 说明 |
| --- | --- |
| `--data-dir PATH` | 输出目录；向 `hoa-major-data` 写入数据时应指向该仓库根目录 |
| `--no-refresh-cookie` | 跳过 CAS 会话刷新，原样使用 `.env` 中的 Cookie |
| `--benchmark` | 完成后打印本次抓取耗时 |

`crawl` 还要求通过 `--years 20XX [20XX ...]` 指定一个或多个年级。例如：

```powershell
uv run jwts crawl --years 2024 2025 --data-dir ..\hoa-major-data --benchmark
uv run jwts grades --data-dir ..\hoa-major-data
```

`--years` 可以填写一个或多个年级。不写 `--data-dir` 时，工具会写入 `src/hoahrb_jwts/data`。更新 `hoa-major-data` 时应像上例一样明确填写 `--data-dir ..\hoa-major-data`。

培养方案类别主要读取专业列表名称末尾的 `【本】`、`【辅修】`、
`【第二学士学位】`。学院代码后的专业段以 `Y` 开头时，作为教务系统错误标签的
覆盖规则，强制归入独立的 `Y` 类；已知错误记录 `01044` 则强制归入本科。
无标签记录中，专业段以 `M` 开头的归入微专业，学院代码后可带两位年份且随后
以 `L` 开头的分流大类归入本科，其余归入未分类。

默认情况下，抓取开始前会请求教务系统的 CAS 地址探测现有会话。该请求不一定返回新的 Cookie：没有更新时继续使用当前 Cookie；返回更新时立即更新正在工作的 HTTP 会话，并用它完成本次抓取。实际认证状态由后续业务接口验证。抓取成功后，如果 Cookie 来自 `.env` 且确实发生变化，才会原样保留其他配置和注释并更新其中的 `HIT_JW_COOKIE`；更新后的值会写成双引号包裹的 dotenv 格式，但请求仍使用不带外层引号的 Cookie 值。若需要排查会话问题、坚持使用填写的 Cookie，可加 `--no-refresh-cookie` 跳过这一步。工具不会自动登录，也不会把 Cookie 写入版本库。

`crawl` 会替换指定年级的 `major_mapping.json` 条目和计划文件，并按本次计划中实际出现的课程代码去重抓取中英文课程简介。本次涉及的课程会增量更新到 `course_introductions.json`，未涉及的已有课程保持不变；它不会改动 `grades_summary.json`。成绩构成由下面的独立命令抓取。

课程简介来自 `/pub/queryKcxxView?kcdm=...` 的“课程简介”和“课程英文简介”字段。详情请求会沿用全局重试策略；任一课程在重试后仍失败时，本次 plans、mapping 和 introductions 均不发布。详情页正常返回空简介属于合法数据。

## 更新培养方案数据

在 `hoahrb-jwts` 仓库运行抓取，并将 `--data-dir` 明确指向相邻的 `hoa-major-data` 仓库根目录：

```powershell
uv run jwts crawl --years 2024 2025 --data-dir ..\hoa-major-data
```

完成后审查生成的数据：

```powershell
git -C ..\hoa-major-data status --short
git -C ..\hoa-major-data diff -- major_mapping.json plans
```

## 修正专业名称或跳过记录

教务系统偶尔会漏掉班型名称，或返回不应发布的记录。这些例外写在 `src/hoahrb_jwts/major_rules.toml`，不要写进 Python 代码。

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

成绩抓取先读取 `/cjcx/queryQmcj` 的成绩列表，再逐个请求列表中放大镜对应的 `/cjcx/queryCjxxView` 详情页，从详情页的“权重（占总成绩百分比）”字段提取成绩构成。它沿用同一份 `HIT_JW_COOKIE`、代理和 CAS 会话刷新逻辑。`grades_summary.json` 由 `hoa-major-data` 仓库持有，因此运行时应将 `--data-dir` 指向该仓库根目录：

```powershell
uv run jwts grades --data-dir ..\hoa-major-data
```

`crawl-grades` 是 `grades` 的别名。抓取结果会校验为 `grades_summary.json` 的现有格式，并以原子方式替换 `hoa-major-data/grades_summary.json`；运行后在 `hoa-major-data` 中审查差异：

```powershell
git -C ..\hoa-major-data diff -- grades_summary.json
```

如果需要从 `repos-management/grades_summary.toml` 生成人工维护版本，仍可使用转换脚本：

```powershell
python scripts\update_grades_summary.py --data-dir ..\hoa-major-data
```

## 向 `hoa-major-data` 贡献成绩数据

`grades` 只抓取当前账号成绩记录里带放大镜详情、且详情明确提供分项权重的课程。它不会导出总成绩或各分项得分。成绩数据应提交到 `hoa-major-data`，而不是 `hoahrb-jwts`。以下命令假定两个仓库位于相邻目录；贡献前请在本地检出的 `hoa-major-data` 仓库中新建贡献分支。

1. 登录教务系统，按 Usage 中的说明更新本地 `.env`。

2. 抓取成绩构成，并将结果写入本地 `hoa-major-data` 根目录：

   ```powershell
   uv run jwts grades --data-dir ..\hoa-major-data
   ```

3. 在 `hoa-major-data` 仓库查看命令输出和数据差异：

   ```powershell
   git -C ..\hoa-major-data status --short
   git -C ..\hoa-major-data diff -- grades_summary.json
   ```

4. 提交前逐项确认：

   - 只修改了 `grades_summary.json`；
   - 新增内容使用课程代码作为键，并写入 `default` 列表；
   - 每个分项形如 `{"name": "期末考试", "percent": "60%"}`；
   - 没有总成绩、个人得分、Cookie 或其他个人信息；
   - 没有删除本次贡献无关的已有课程。

`grades` 会按当前账号可见数据原子替换 `hoa-major-data/grades_summary.json`。若 diff 中出现其他贡献者课程被删除，必须先保留这些已有条目，只合入本次新增或确认更新的课程，不能直接提交整份删除结果。

确认无误后，在 `hoa-major-data` 仓库提交，并向 `hoa-major-data` 发起 Pull Request：

```powershell
git -C ..\hoa-major-data add grades_summary.json
git -C ..\hoa-major-data commit -m "data: update HIT grade components"
```

## 开发验证

```powershell
make check
make build
```
