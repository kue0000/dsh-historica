# historica v1.1.0 · DSH 迁移说明

面向 DSH「创造模式」的 agent：这份文档足以在另一台机器（或另一个目录）把本插件重建并跑通，不需要读原作者的对话记录。
包内**没有任何本机绝对路径**，解压目录即插件根。

---

## 1. 插件能做什么

给国内任意范围（行政区名 / bbox / GeoJSON·Shapefile·KML / WKT / 网页框选）和若干目标年份，走一条 CLI 流水线：

**查景 → 选景 → 估时（不下载）→ 窗口化下载 → 色调统一 → 配准校验 → 多期对比图 + 技术报告**

- 数据源默认 Microsoft Planetary Computer 的 **Landsat Collection 2 Level-2**（公有领域，30 m）；可选 Sentinel-2 L2A（默认关闭）。
- 不下载整景：按目标窗口对 COG 做 HTTP Range GET，只取需要的块。
- 只做展示级色调统一，**不做定量反演/变化检测**。

明确的**能力边界**（写进任何交付物，不得删改）：

| 不是 | 说明 |
|---|---|
| 不是历史地形图扫描件库 | 只处理卫星影像 |
| 不承诺 1984 年前 | 本插件只承诺 1984 年后的 TM/ETM+/OLI 可用性 |
| 不承诺跨 100 km 单景完整 | WRS-2 单景约 170×183 km，跨轨缝必然多景多日 |
| 不适合判读地块边界 | 30 m 分辨率只够看建成区、道路、水库等宏观地物 |
| 不保证绝对定位 | Landsat C2 约 12 m RMSE；行政区边界本身是示意图（DataV 百米级、GADM 百米级） |

---

## 2. 目录约定

```
<插件根>/
├─ historica.py            # CLI 入口（唯一可执行脚本）
├─ hist/                   # 核心库（14 个模块，无第三方硬依赖以外的隐式依赖）
├─ config/
│  ├─ default.json         # 全部可调参数（选景权重、季节锁、预估、看门狗、色调、出图）
│  └─ cases/*.json         # 四个内置案例的 defaults（相对路径）
├─ scripts/picker.html     # 纯前端框选页（Leaflet CDN；可离线打开，底图需联网）
├─ tests/test_core.py      # 36 个单测，全部离线（合成 plan，不发网络请求）
├─ tests/data/plan_yanping.json  # 延平真实计划（离线 estimate 体检用夹具，228 KB）
├─ docs/                   # 设计、限制、吞吐实测、第三地形验证
├─ README.md  SKILL.md  CHANGELOG.md  DSH-迁移说明.md  SELFTEST.txt
```

**运行期自动生成**（不进交付包，也不要提交到任何仓库）：

```
runs/<case>/estimate/<时间戳>/   plan.json / estimate.json / config_used.json
                                 （fetch 后）actual.json / fetch_progress.json / align.json
runs/<case>/estimate/<时间戳>/data/  epoch_<年>.npz + epoch_<年>.json
runs/<case>/estimate/<时间戳>/out/   series.jpg / single_<年>.jpg / metadata.csv / technical_report.md
runs/<case>/estimate/<时间戳>/cache/raw/<item_id>/<band>_<hash>.npz
cache/calibration.json              # 吞吐校准表（插件根下，跨 run 复用）
```

路径规则（关键）：

- 插件根由 `hist/config.py` 的 `ROOT = dirname(dirname(os.path.abspath(__file__)))` 运行期推导，**与安装位置无关**。
- `--out` 默认 `runs`（相对**当前工作目录**）；`<out>/<...>/cache` 是影像缓存。
- 案例 `config/cases/*.json` 的 `out` 是相对路径（`runs/<case>`）。
- **唯一需要显式指定的路径配置**：`estimate.calibration.path`，默认 `cache/calibration.json`，相对插件根解析；写绝对路径可以把多台机器的样本汇到一处（可选，不是必需）。

---

## 3. Python 版本与依赖

- **Python 3.10+**（开发与验证环境为 3.12；用到 `str | None` 之外的现代语法不多，但 3.10 以下未测）。
- Windows 上 `python -m venv .venv` 后执行 `.\.venv\Scripts\python.exe`；类 Unix 为 `.venv/bin/python`。

**标准库（无需安装）**：`argparse json os sys time csv hashlib statistics math re copy datetime zipfile struct zlib urllib.request urllib.error urllib.parse concurrent.futures xml.etree`。

**必须安装（缺任何一个核心功能不可用）**：

| 包 | 用在哪 | 缺失后果 |
|---|---|---|
| `numpy` | 全流程数组运算、npz 缓存 | 无法启动 |
| `shapely` | 边界几何、足迹并集、覆盖率 | 无法解析范围 |
| `pyproj` | UTM / WGS84 转换 | 无法建格网 |
| `matplotlib` | `compose` 出图 | `compose` 失败（`estimate/fetch/report` 仍可用） |

**可选（缺失自动降级，装上是推荐配置）**：

| 包 | 作用 | 缺失时 |
|---|---|---|
| `rasterio` | 本地/远程 COG 读取 | 自动走 `hist/coghttp.py` 纯 HTTP 读取 |
| `pystac-client` | STAC 检索 | 自动走裸 STAC `/search` |
| `pyshp` | `--vector *.shp/*.zip` | 读 shapefile 时报错，GeoJSON/KML 不受影响 |

推荐一次装齐：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install numpy shapely pyproj matplotlib rasterio pystac-client pyshp
```

**系统层面还需要**：

1. **对外 HTTPS 出网**（默认端口 443）：`planetarycomputer.microsoft.com`（STAC + 影像资产，走 SAS 重定向到 Azure Blob）、`geo.datav.aliyun.com`（行政边界）、`geodata.ucdavis.edu`（GADM 回退）。三者缺一都会让对应功能不可用。
2. **CJK 字体**：`config/default.json → compose.font` 默认 `Microsoft YaHei`，回退链 `SimHei → DejaVu Sans`。Windows 自带；Linux 容器里要装 `fonts-noto-cjk` 之类，否则中文标注会显示成方框（不报错、图能出）。
3. **磁盘**：每个案例 `cache/raw` 是主要占用（珠三角核心四期约 1 GB 量级，花溪约数百 MB）。运行目录建议放在有 ≥2 GB 余量的位置。
4. 不需要 QGIS/GDAL 命令行；GDAL/PROJ 数据路径在 `hist/__init__.py` 导入时自动修正。

---

## 4. 五条子命令的参数与退出码契约

统一入口：`python historica.py [全局参数] <子命令> [参数]`。

**全局**：`--version`（打印 `historica 1.1.0` 后退出 0）、`--case <name>`（把 `config/cases/<name>.json` 的 `defaults` 作为 argparse 默认值；**命令行显式参数优先**）。

**通用参数**（`init`/`estimate`/`fetch`/`compose`/`report` 都有）：`--config <path>`（覆盖配置文件，与默认深度合并）、`--out <dir>`（默认 `runs`）、`--res <m>`、`--crs <epsg|auto-utm>`、`--provider <landsat-c2-l2|sentinel-2-l2a>`。

### 4.1 `init`

```
python historica.py init --out runs/demo
```
- 作用：建 `cache/ data/ out/`，写 `config_used.json`。
- 不联网、不选景。
- 退出码：`0` 成功；`2` 参数错误；其他非零 = 异常（附 traceback）。

### 4.2 `estimate`（选景 + 测速 + 算账，**不下载**）

范围四选一：`--admin` | `--bbox W,S,E,N` | `--vector` | `--wkt`。
年份：`--year 1993,2004` | `--range 1990-2025 --step 5`；`--window N`（默认 1）；`--prefer-date 1993-12-26`（可重复）。
季节：`--season-window N`（默认取 `selection.season_window_days = 45`；**0 = 关闭，允许跨季**）。
测速：`--dry-run`（本子命令本来就是 dry-run，`--dry-run` 仅作显式声明）、`--no-probe`（不测速，用配置兜底）、`--speed <MB/s>`（手动指定，跳过测速）、`--plan <plan.json>`（**跳过 STAC 查询**，用已有计划，便于离线/复算）。

产物：`runs/<case>/estimate/<时间戳>/{plan.json,estimate.json,config_used.json}`。
退出码：`0` 成功；`2` 参数错误（范围解析失败 / 年份非法）；其他非零 = 异常。

### 4.3 `fetch`（按计划窗口下载并拼接）

```
python historica.py fetch --plan runs/<case>/estimate/<ts>/plan.json
```
- 不给 `--plan` 时会在 `fetch/<时间戳>/` 现场做一次选景并写 `plan.json`（等价于跑一次不带测速的 estimate）。
- `--only-year 1993,2024`：只重建指定年份，其余期命中缓存直接跳过。
- 产物：`data/epoch_<年>.npz`、`data/epoch_<年>.json`、`actual.json`、`fetch_progress.json`、`out/metadata.csv`（每期增量落盘）。
- 退出码：`0` 全部完成；`3` 运行期看门狗暂停（已落盘、可原样续跑）；`2` 参数错误；其他非零 = 异常（看 `fetch_progress.json`）。

### 4.4 `compose`（色调统一 + 配准校验 + 出图）

```
python historica.py compose --run runs/<case>/estimate/<ts>
```
- 读 `data/epoch_*.npz`；写 `out/series.jpg`、`out/single_<年>.jpg`、`align.json`。
- 退出码：`0` 成功；`1` run 目录下没有 `data/epoch_*.npz`（需先 `fetch`）；`2` 参数错误。

### 4.5 `report`（元数据 CSV + 技术报告 + 实测回写）

```
python historica.py report --run runs/<case>/estimate/<ts>
```
- 读 `plan.json` / `estimate.json` / `actual.json` / `align.json`（后三者缺失时降级为“无实测/无配准”版本）。
- 写 `out/metadata.csv`、`out/technical_report.md`；把本次净吞吐（+ 看门狗分块吞吐）写入 `cache/calibration.json`。
- 退出码：`0` 成功；`2` 参数错误。

> 退出码汇总：`0` 成功 · `1` compose 缺 npz · `2` 参数错误（stderr 以“参数错误:”开头）· `3` 看门狗暂停、可续跑 · 其他 = 未预期异常。

---

## 5. 缓存与断点续传

**影像缓存**

- 键：`<run>/cache/raw/<item_id>/<band>_<sha1(窗口边界+EPSG)[:16]>.npz`。
- 每个波段读完立即落盘；中断后原样重跑 `fetch --plan`，已完成的波段直接命中缓存，不会重下。
- 换分辨率/换范围会生成新键，不会误用旧缓存（因此改 `--res` 等于重下）。
- 换目录不会迁移缓存（缓存跟着 run 目录走）；跨机器迁移只迁代码，不迁缓存。

**断点续传**

- 看门狗：下载期连续 3 个“单波段窗口读取”的等效吞吐低于预估区间低端（p5）的 1/2 → 先落盘当前波段，再写 `fetch_progress.json`（`completed_years` / `paused` / `reason`），退出码 **3**。
- 恢复方式：同一条 `fetch --plan <同一份 plan.json>` 原样重跑；已完成的期会跳过（也可用 `--only-year` 精确指定）。
- `actual.json` 记录每期耗时、有效覆盖、云/卷云扣除与看门狗吞吐样本；`report` 会把它并入校准表。

**吞吐校准**

- `cache/calibration.json` 保留最近 100 条，估算只用最近 `estimate.calibration.window = 5` 条。
- 样本池 = 本轮并发探针轮次 + 最近 5 条净吞吐 + 这些运行看门狗记录的分块吞吐（每条最多等距抽 10 个），各样本等权。
- 点估计 = 中位；区间 = **p5–p95**；样本 < 4 时回退 `estimate.time_range = [0.8, 1.3]` 并标注。

---

## 6. 三个已知案例怎么跑

前置：所有命令在插件根执行，`PY=.\.venv\Scripts\python.exe`。

### 6.1 延平区（闽北山地；跨轨缝 + 薄卷云）

```powershell
$PY historica.py estimate --case yanping
$PY historica.py fetch    --plan runs/yanping/estimate/<ts>/plan.json
$PY historica.py compose  --run  runs/yanping/estimate/<ts>
$PY historica.py report   --run  runs/yanping/estimate/<ts>
```
- 规模：四期（1993/2003/2012/2024），1993 强制 `1993-12-26`；下载量约 1 GB 级。
- 验收：`out/series.jpg` 存在；`out/technical_report.md` 含“候选排名”与“配准校验”；1993 期显式标出缺角面积。
- 已知结论：1993 跨轨缝缺角同季（真实区界覆盖约 75%）；2003 薄卷云扣除约 310 km²。

### 6.2 珠三角核心（广深莞；一次看门狗暂停后续跑）

```powershell
$PY historica.py estimate --case prd_core
$PY historica.py fetch    --plan runs/prd_core/estimate/<ts>/plan.json
# 若返回 3：网络恢复后原样重跑同一条 fetch
$PY historica.py compose  --run  runs/prd_core/estimate/<ts>
$PY historica.py report   --run  runs/prd_core/estimate/<ts>
```
- 规模：约 102×90 km、9,206 km²，四期，下载约 1 GB 级。
- 验收：`fetch_progress.json` 里能看到暂停/恢复记录时 `actual["paused"]` 曾为 true；2012 期可读约 78.8%。

### 6.3 贵阳花溪（云贵高原湿润区；季节锁验证）

```powershell
# v1.1 默认：同季 ±45 天
$PY historica.py estimate --case guizhou_huaxi
# 对照 v1.0 行为：关闭季节锁
$PY historica.py estimate --case guizhou_huaxi --season-window 0
```
- 规模：区界约 964 km²，四期，下载约数百 MB。
- 验收：默认输出里四期日期落在 10–11 月，报告 1.1 节给出年内日序标准差（封版实测 16.2 天）与最大差（42 天）；`--season-window 0` 时 std 约 101 天、最大差 250 天。
- 代价（如实告知）：同季锁让 2003 期可读从 97.8% 降到 93.8%、2012 期从 98.1% 降到 92.0%，平均 −2.5 pp。

> 不放 `runs/` 是本包的既定取舍：成品图与技术报告可由上述命令重建；封版实测数字已写进 `CHANGELOG.md` 与 `docs/validation-3rd-terrain.md`，可直接引用。

---

## 7. 迁移自检（本包在干净目录的实测结果）

见本包根目录 `SELFTEST.txt`（解压后即可查看原始输出），摘要（Python 3.12.13、numpy 2.5.4 / shapely 2.2.0 / pyproj 3.8.0 / matplotlib 3.11.2）：

- `--version` → `historica 1.1.0`，退出码 0
- `python -m unittest discover -s tests -q` → `Ran 36 tests ... OK`，退出码 0
- `estimate --plan tests/data/plan_yanping.json --no-probe --speed 0.2 --out selftest_runs` → 退出码 0；
  四期合计 711.1 MB 原始 / 362.0 MB 传输、点估计 39.2 min、区间 31.3–50.9 min（无探针样本时按设计回退 `time_range` 系数），**未产生任何 `data/epoch_*.npz`**

若在 DSH 侧重建，请先复跑这三步，再动任何代码。

---

## 8. 可直接粘贴给「创造模式」的提示词

```
你拿到的是一个可整体迁移的 Python CLI 插件包 historica v1.1.0（历史卫星影像多期对比）。
请先在当前环境把它跑通，再按需改造，不要重写架构。

【第一步：环境与验收（必须先做，全部通过才继续）】
1. 解压 zip 到任意干净目录 <root>（目录内应看到 historica.py / hist/ / config/ / tests/ / docs/）。
2. 建虚拟环境并安装依赖：
   python -m venv <root>/.venv
   <root>/.venv/bin/python -m pip install numpy shapely pyproj matplotlib rasterio pystac-client pyshp
   （Windows 用 .venv\Scripts\python.exe）
3. 在 <root> 内依次执行并记录真实输出：
   a) python historica.py --version            → 期望 "historica 1.1.0"，退出码 0
   b) python -m unittest discover -s tests -q  → 期望 36 个测试全过，退出码 0
   c) 任取一个真实 plan.json 做离线算账：
      python historica.py estimate --plan tests/data/plan_yanping.json --no-probe --speed 0.2
      期望退出码 0、打印“合计”流量与耗时区间、不产生 data/epoch_*.npz
4. 检查没有本机绝对路径残留：
   在 <root> 内搜索 "C:\\Users" 与 "temp_2026"，应只在 docs/ 的历史运行目录说明里出现（那是数据出处，不是依赖）。

【第二步：弄清契约】
阅读 DSH-迁移说明.md 与 SKILL.md，确认你理解：
- 五条子命令 init/estimate/fetch/compose/report 的输入输出与退出码（0 成功 / 1 compose 缺数据 / 2 参数错误 / 3 看门狗暂停可续传）；
- 缓存键规则（<item_id>/<band>_<sha1(窗口+EPSG)>.npz）与断点续传；
- 季节锁默认开启（--season-window 45），0 才允许跨季；窗口内无低云景时标 cross_season，不得静默放开。

【第三步：产出（按你收到的具体任务二选一或都做）】
A. 迁移式交付：在目标环境按上面的验收跑通，提交一份 <root>/MIGRATION-REPORT.md，
   写清：实际版本输出、单测数量与结果、estimate 的退出码与关键数字、安装耗时、遇到的依赖/网络/字体问题及解决方式。
B. 改造式交付：在不改变 CLI 契约与退出码的前提下做增量（例如新增数据源 provider、增加输出格式）。
   必须：单测保持全绿（新增逻辑补测试）；不得引入新的本机绝对路径；不得改动 runs/ 下既有成果目录；
   文档同步更新 README 的对应章节与 CHANGELOG。

【验收标准（逐条勾】】
- --version 打印 1.1.0 且退出 0；
- unittest 全部通过（数量不少于 36）；
- estimate 在 --no-probe 下退出 0，且输出含“合计”；
- 代码/配置/文档无硬编码本机路径（唯一允许可覆盖项：estimate.calibration.path）；
- 五条子命令参数与退出码与 DSH-迁移说明.md 第 4 节一致；
- 若做了改造：报告里必须如实写出代价与未覆盖场景，不得声称“既满覆盖又同季”。
```

---

## 9. 已知限制（迁移时不要试图“修好”）

完整 10 条见 `docs/known-limits.md`。迁移最容易被误判的四条：

1. **跨季与满覆盖不可兼得**：这是选景的物理约束，不是 bug。
2. **配准未锁定**（NCC 置信比低 / 峰值贴边）时只报“未锁定”，**不给位移值**，也不自动纠偏。
3. **云掩膜边界不羽化**：QA_PIXEL 是 30 m 硬边界，图面边缘会有锯齿；不要用插值“美化”，那会污染可读覆盖统计。
4. **OCR/年份归属不确定**：图上与报告只写**实际成像日期**，不把“目标年”当事实。
