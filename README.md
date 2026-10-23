# hoahrb-jwts

这是给维护者用的命令行工具，不是给普通用户安装的应用。它从哈工大本部教务系统读取指定年级的执行教学计划和公开成绩构成，并更新 `hoa-major-data` 仓库中的数据文件：

> **与原 HITSZ 教务工具的关键区别：本项目已重构为无状态的培养方案采集器，只负责抓取并输出数据；原有的查询等便捷功能不再提供。**

- `major_mapping.json`：年级、学院和专业的目录；
- `plans/*.toml`：每个专业的课程列表。
- `grade_contributions/*.json`：当前账号可见的课程成绩构成贡献，合入对应方案的 `grade_details`。
- `grades_summary.json`：独立维护的资料仓库成绩汇总，不由个人 `grades` 抓取覆盖。
- `course_introductions.json`：培养方案中课程的中英文简介。

它不会自动输入账号密码，也不会向教务系统提交业务数据。启动发现登录态失效且无法通过已有统一认证会话恢复时，会打开独立的浏览器窗口供维护者手动登录。正确的流程是：本地抓取 → 查看数据 diff → 人工确认后提交 `hoa-major-data`。

## Usage

### 准备环境

需要 Python 3.14 或更高版本以及 [uv](https://docs.astral.sh/uv/)。克隆仓库后安装项目和开发依赖：

```powershell
uv sync --dev
# 系统已有 Edge/Chrome 时，无需安装额外浏览器
```

在 `hoahrb-jwts` 仓库创建本地配置：

```powershell
Copy-Item .env.example .env
```

`.env` 只配置站点、超时和代理；首次运行或使用 `--relogin` 时通过浏览器登录。凭据只保存于独立会话 JSON，不再读取或回写 dotenv 中的 Cookie。会话文件不可提交。

常用配置如下：

| 变量 | 必需 | 说明 |
| --- | --- | --- |
| `HIT_JW_SESSION_FILE` | 否 | 独立会话文件，默认 `.env.cookies.json` |
| `HIT_JW_BASE_URL` | 否 | 教务系统地址，默认 `http://jwts.hit.edu.cn` |
| `HTTP_PROXY` / `HTTPS_PROXY` | 否 | 访问教务系统所需的代理 |
| `HIT_JW_TIMEOUT_SECONDS` | 否 | 单次请求超时，默认 20 秒 |
| `HIT_JW_DELAY_SECONDS` | 否 | 连续请求间隔，默认 0.1 秒 |
| `HIT_JW_MAX_RETRIES` | 否 | 可重试请求次数，默认 3 次 |

### 认证恢复与持久化

启动先验证当前教务会话，有效时不请求 `/loginCAS`。业务请求遇到明确的认证要求时，最多恢复一次并重试一次；只允许配置的教务站点与学校 HTTPS 统一认证站，最多 8 跳。Cookie 按域、路径、Secure 和有效期发送，支持服务端删除指令；不会把教务 Cookie 转发给统一认证站。脚本或 meta 登录跳转按认证失败处理，不当作 JSON 解析错误。网络故障、HTTP 403 和 HTTPS 证书错误不会弹出登录窗口。

需要人工登录时优先通过 Playwright 复用系统 Edge/Chrome，使用独立登录上下文。请在窗口内自行完成登录和验证码；验证业务页面后才导入、持久化 Cookie 并关闭窗口。不读取个人浏览器凭据、不保存密码。需要可用的桌面会话；关闭窗口或等待超过 5 分钟时退出认证流程。运行中的查询不会反复打开浏览器。`--relogin` 主动打开浏览器重新认证，并复用已保存的两站 Cookie；统一认证仍有效时可自动跳回，验证成功后保存并关闭窗口。

凭据仅保存在 `.env.cookies.json`（可由 `HIT_JW_SESSION_FILE` 指定），包含两站 Cookie 的完整属性；dotenv 和进程环境中的旧 Cookie 字段不再读取，配置文件不再回写。会话文件原子替换，写入锁和版本检查防止并发覆盖；旧版 JSON 会话验证成功后转存新格式，不依赖 dotenv 指纹。业务验证成功就保存，不因长任务后续失败而丢失有效刷新。删除会话文件可重新开始登录，自定义路径须自行排除 Git 跟踪。

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
| `--relogin` | 打开浏览器重新认证，复用已保存的两站 Cookie，验证成功后保存 |

例如：

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

默认启动先验证业务页，必要时自动恢复认证。需要主动重新认证时使用 `--relogin`，它载入已保存的两站 Cookie；统一认证仍有效时可直接跳回，验证成功后保存并关闭窗口。会话只保存在独立 JSON 文件。

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

## 中断后继续抓取

`crawl` 默认逐页缓存教学计划、逐门缓存课程简介。网络中断、Ctrl+C 或发布校验失败后，直接重跑相同命令即可继续；保持相同的 `--data-dir` 和年级集合，已成功抓取并解析的响应会显示 `checkpoint hit`，无需再次请求。修改专业名称规则后也会重新解析缓存并应用新规则。

缓存默认位于本项目的 `.cache/jwts/`（已由 Git 忽略），不保存 Cookie 或响应头。发布成功后自动清理本次缓存，下次正常抓取会获取最新数据。此前版本没有缓存，旧运行的进度无法恢复。

需要丢弃未完成的进度、全部重新抓取时，在原命令后加 `--fresh`；可用 `--cache-dir <目录>` 指定缓存位置。此功能适用于 `crawl`，不包含 `grades`。

## 修正专业名称或跳过记录

规则依据、2+X 领域的课程识别方法及未确认例外见 [专业规则依据与复查](docs/major-rules.md)。

同名方案冲突时，兜底文件继续使用 `_专业代码.toml` 格式，例如 `本_2023_未来技术学院_精密仪器_52WL014Z.toml`；数据中的专业名称追加中文括号包裹的代码，例如 `精密仪器（52WL014Z）`，并同步到 `major_mapping.json`。不冲突的名称保持不变，不合并课程或绕过 `publish = false` 规则。

添加 `--no-filename-fallback` 可禁用兜底，同名冲突时停止发布并保留断点缓存。成功运行末尾会打印 `filename fallbacks: files=N` 和本次发布范围内所有兜底文件名；没有兜底时显示 `files=0`。国际生 Y 代码原有的固定后缀不计入兜底数量。

专业代码以 `W` 结尾表示威海拔尖班，程序会在名称缺少该班型时自动追加 `（威海拔尖班）`。普通班与威海拔尖班分别保留，即使课程完全相同也不合并。

教务系统偶尔会漏掉班型名称，或返回不应发布的记录。这些例外写在 `src/hoahrb_jwts/major_rules.toml`，不要写进 Python 代码。

每条规则必须同时写 `year` 和 `code`。`year = "*"` 表示所有年级；指定年级的规则优先于该代码的通用规则。例如：

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

- `year`：一个年级、多个年级组成的列表，或 `"*"` 表示所有年级；
- `code`：教务系统返回的专业代码；
- `name`：仅在需要覆盖教务系统名称时填写；
- `category`：仅在需要覆盖来源误标的专业类别时填写，例如 `"本科国际生"`；
- `filename_name`：仅在需要保持原文件命名时填写；独立于数据中的 `name`，同名文件仍使用 `_专业代码.toml` 消歧义；
- `publish = false`：不生成这条记录；
- `reason`：写明判断依据，便于后续维护。

改完规则后，至少运行：

```powershell
uv run pytest tests/unit/test_discovery.py -q
uv run ruff check src tests
```

## 抓取并合入成绩贡献

本次工作流的完整命令帮助（包含默认路径、同步行为、数据保留规则及示例）：

```powershell
uv run jwts --help
uv run jwts crawl --help
uv run jwts grades --help
uv run jwts crawl-grades --help
uv run jwts merge-data --help
uv run python scripts/update_grades_summary.py --help
```

`crawl` 抓取培养方案与课程简介；`grades`（别名 `crawl-grades`）只抓当前账号可见的课程成绩构成。两者的覆盖范围不同。个人抓取只提取分项名称和权重，不导出总成绩、分项得分、姓名或学号。

```powershell
uv run jwts grades
```

在本机直接运行 `uv run jwts grades` 即可：默认定位 jwts 仓库旁的 `hoa-major-data`，不存在时从 `HOAHRB/hoa-major-data` 克隆；存在时获取 `origin/main` 并在安全时快进，再抓取当前账号的成绩贡献并合入本人方案。不必填写路径或年级、专业代码；需要登录时按浏览器提示完成认证。

以上路径适用于源码检出后 `uv run`。如果使用 wheel 安装后的 `jwts` 命令，默认以运行时当前目录作为工作目录：培养方案输出为 `./jwts-data`，major data 为 `./hoa-major-data`，`.env` 与会话文件也从工作目录读取，不向虚拟环境或 `site-packages` 写入运行数据。两种模式都支持显式指定目录。

如果远端有新提交而本地有未提交修改或分支已分叉，会停止并保留所有内容，不自动 stash、重置或解决 Git 冲突。远端没有新提交时允许继续积累本地贡献。程序不会自动提交或推送。

`--data-dir PATH` 可指定其他输出位置；显式指定非 Git 目录时不联网同步。`--no-sync` 可跳过同步并使用本地副本。提交时同时包含对应方案和 `grade_contributions/`；冲突复核记录位于 `grade_conflicts/`。

程序从个人信息页读取年级、学院和专业，并从当前账号执行教学计划页的服务端默认选中项读取精确专业代码（`pageZydm`），同时核对年级和学院。该请求不带筛选参数、不使用断点缓存，不按同名专业猜测。确认身份后只将成绩写入该方案课程的 `grade_details`；不同专业同课号不互相填充。默认代码缺失或身份字段不一致时拒绝写入。

抓到的贡献独立保存在输出目录的 `grade_contributions/<内容哈希>.json`，包含方案身份与成绩构成。冲突或目标方案缺失时仍保留贡献文件，后续可以再次合并；无需重新抓取。该文件不包含学号、姓名、个人成绩或会话凭据。本地有方案时会尝试立即合入；没有方案时先保留贡献，并提示方案缺失。

专业课程页面读取方案 TOML 内的 `grade_details`。`grades` 不再向中央 `grades_summary.json` 写入个人课号；中央文件按资料仓库 ID 使用，仍由原有维护流程管理。

## 将本地抓取结果合入 hoa-major-data

本地 `crawl` 结果可以完全不含成绩；合并时以已有 `hoa-major-data` 为基底保留他人的贡献。`merge-data` 默认自动同步相邻仓库，再执行合并，不能用复制目录或覆盖整个文件代替：

```powershell
uv run jwts merge-data
git -C ..\hoa-major-data diff --stat
```

合并规则：

- 按年级和专业代码匹配方案，按课程代码保留目标方案内已有的 `grade_details`。专业或文件改名不改变关联。
- 新成绩只写入贡献文件指定的方案；已有不同成绩构成时保留目标内容并汇报冲突，贡献原件保留供复核。
- 来源没有的方案和课程简介保留；来源有的方案更新基础课程信息。若更新会移除含成绩详情的课程，发布前报错，目标不变。
- 未变化的方案保持原始字节，不重排、不重写；仅修改发生变化的文件。重命名只移除同一方案的旧文件名，不批量删除目标目录。
- 中央成绩汇总、查询表、共享分类和其他手工文件不被本地目录覆盖。
- 写入异常时回滚；回滚失败时保留 `.jwts-merge-recovery.json` 恢复副本。不要同时运行多个写入命令。

历史遗留的未绑定方案成绩贡献会阻止该次合并，应先核实精确方案代码。当前抓取直接读取账号默认专业代码，不依赖同名候选匹配。

`crawl --data-dir ..\hoa-major-data` 直接重抓时也保留已有 `grade_details`；若课程或方案消失会导致贡献丢失，则停止发布并报告标识。课程简介继续增量合并。

旧的人工成绩转换脚本仍可使用：

```powershell
uv run python scripts\update_grades_summary.py --data-dir ..\hoa-major-data
```

该脚本按课程／变体增量合并中央汇总，保留未涉及条目，对差异保留已有值并汇报；已有文件损坏时拒绝覆盖。

确认差异后再提交 `hoa-major-data` 中的方案及贡献文件。多人同时贡献时，先合入远端新增提交，再重跑 `merge-data`，不要用个人快照解决冲突。

## 开发验证

```powershell
make check
make build
```

浏览器按系统 Edge → 系统 Chrome → 已安装的 Playwright Chromium → 已安装的 Playwright WebKit 顺序尝试，仅启动失败时切换。不再尝试 Playwright Firefox，也不会自动下载浏览器；系统已有 Edge/Chrome 时无需额外安装。没有可用引擎时，可自行运行 `uv run playwright install chromium` 安装备用引擎。保留引擎原生 UA，设置 zh-CN、Asia/Shanghai 和 1366×768 视口，使用可见窗口；不使用固定旧版或跨引擎 UA。验证码由维护者手动完成。

CLI 启动后记录脱敏认证日志 `data/jwts-auth.log`，每份约 2 MB，保留 3 份轮转备份。记录 Cookie 数量、HTTP 状态、受限跳转路径、CAS 恢复和保存结果；不记录 Cookie 值、查询参数、CAS ticket 或响应正文。

一次性重新登录（不保留启动开关）：

```powershell
uv run --script relogin.py --help
uv run --script relogin.py
uv run --script relogin.py --fresh
```

默认复用保存的会话；`--fresh` 忽略两站旧 Cookie，从空会话登录。取消或验证失败保留原文件；成功后原子替换，期间其他进程修改会话会拒绝覆盖。支持 `--env-file PATH`（默认 `.env`）。抓取命令仍可使用 `--relogin`；其原有 `--fresh` 参数仅用于清除抓取断点，不改变登录方式。

恢复 Cookie 时保留 HttpOnly 的实际值；CAS 语言偏好 Cookie 由网页重新生成，避免旧缓存引发循环。5 秒内至少 8 次主页面跳转时，在同一窗口清空 Cookie 重试一次；仍循环则停止，不覆盖原文件。

认证导入与持久化支持 `.hit.edu.cn` 父域 Cookie（例如 `__access_token`），保留域、路径、有效期和 HttpOnly/Secure 属性；HTTP 客户端仅向配置的教务站点和 CAS 发送匹配的 Cookie。旧版曾丢弃父域 Cookie，需重新登录后才能补齐；无法从旧 JSON 恢复丢失的值。
