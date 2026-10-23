# Changelog

## 2.0.0 — 待发布

### 不兼容变化与迁移

- `grades` / `crawl-grades` 不再将个人课号写入中央 `grades_summary.json`。改为保存 `grade_contributions/<内容哈希>.json`，并合入本人培养方案内的 `grade_details`。中央汇总仍用于资料仓库维度的共享分类页。
- `grades` 默认输出从 jwts 本地数据目录改为项目相邻的 `hoa-major-data`。默认自动克隆或同步 `origin/main`；需要原有本地输出位置时，显式使用 `--data-dir src/hoahrb_jwts/data --no-sync`。
- 自动同步只做安全快进。存在远端更新且本地有未提交修改或提交分叉时停止，不自动 stash、重置、提交或推送。`--no-sync` 跳过 Git 同步。
- 合入已有 major data 请使用 `merge-data`，不要用本地抓取目录整份覆盖。该命令默认同步相邻仓库；显式指定来源、目标并加 `--no-sync` 可纯本地合并。

### 成绩贡献保护

- 从当前账号执行教学计划页的默认选中项读取精确年级、学院和专业代码，并核对个人信息，避免同名专业误匹配；身份请求不使用断点缓存。
- 成绩只写入对应年级和专业的课程，其他专业同课号不自动填充；贡献文件不保存姓名、学号、个人分数或认证凭据。
- 按年级、专业代码和课程代码保留已有 `grade_details`。相同内容不改写；差异保留目标值，并保留来源贡献及复核记录。
- `crawl` 或合并如果会移除已有成绩详情，发布前停止。专业改名仍按代码关联，不丢失成绩详情。
- 合并保留来源缺少的方案和简介，未变化方案保持原始字节；写入失败回滚，保留中断恢复日志，互斥锁防止两个合并同时运行。
- 人工中央汇总转换脚本改为增量合并，保留未涉及课程和变体；已有文件损坏时拒绝覆盖。

### 命令与文档

- 新增 `jwts merge-data`，支持 `--source`、`--data-dir`、`--no-sync`。
- 更新顶层、`crawl`、`grades`、`crawl-grades`、`merge-data` 和人工转换脚本的完整帮助与示例。
- `jwts --version` 显示实际安装版本。
- 构建包排除本地抓取数据和缓存。wheel 安装模式将运行数据放在当前工作目录（`jwts-data/`、`hoa-major-data/`），不写入安装环境；源码模式保留既有目录布局。

### 推荐流程

贡献本人课程成绩：

```powershell
uv run jwts grades
```

更新培养方案与课程简介：

```powershell
uv run jwts crawl --years 2023 2024 2025 2026
uv run jwts merge-data
```

纯本地合并：

```powershell
uv run jwts merge-data --source ./export --data-dir ../hoa-major-data --no-sync
```

发布前确认方案与贡献文件的差异，并单独提交 major data。本程序不代替 Git 提交或推送。
