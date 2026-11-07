# historica v1.1

> 一句话：给国内任意地区一个范围，自动找 1990s 以来的 Landsat 影像、按真实带宽算账、只下窗口、拼成多期对比图，并附一份写清缺测与误差的技术报告。

数据默认来自 Microsoft Planetary Computer 的 **Landsat Collection 2 Level-2**（免费、公有领域，30 m）。可选 Sentinel-2 L2A（2015 年后）。不下载整景，只按目标窗口做 COG Range GET。

---

## 1. 装在哪

本包不绑定任何本机路径。解压后整个目录就是一个插件根，命令在**插件根目录**下运行：

```
<插件根>/            # 解压得到，例如 skills/historica/
├─ historica.py
├─ hist/  config/  scripts/  tests/  docs/
```

```powershell
cd <插件根>                                    # 只在插件根目录内工作
python -m venv .venv                           # 或 uv venv --python 3.12 .venv
.\.venv\Scripts\python.exe -m pip install numpy shapely pyproj matplotlib pyshp pystac-client rasterio
```

- 代码内的路径全部由 `hist/config.py` 的 `ROOT = dirname(dirname(__file__))` 运行期推导，无 `C:\...` 硬编码。
- 运行目录 `--out` 默认 `runs`（相对当前工作目录）；缓存目录 `<out>/<case>/cache`。
- **唯一需要显式配置的路径**是吞吐校准表 `estimate.calibration.path`（默认 `cache/calibration.json`，相对插件根解析；也可写绝对路径把多台机器的样本共享到一处）。改了它就会改变预估样本池来源，见 [DSH-迁移说明.md](DSH-迁移说明.md)。
- 案例配置 `config/cases/*.json` 里的 `out` 也都是相对路径（`runs/<case>`），换机器不需要改。

依赖都是可选的：没有 rasterio 自动走纯 HTTP COG（`hist/coghttp.py`）；没有 pystac-client 自动走裸 STAC `/search`。

---

## 2. 在 Trae 里怎么触发

安装后直接说人话即可命中本 skill，不需要记命令：

- “帮我看看 **南平市延平区** 1993 年和 2024 年的卫星影像变化”
- “拉 **珠三角** 1993/2003/2012/2024 四期 Landsat 对比图”
- “**贵阳花溪区** 这些年城市扩张成什么样，要带缺测标注”
- “用这个 bbox `113.55,22.45,114.55,23.26` 出四期影像”
- “先别下载，**算一下** 这个范围四期要多少流量、多久”

命中关键词：历史影像、卫星影像对比、Landsat、多期影像、城市扩张、老影像下载、缺测/预估算账。
如果只想要扫描老地形图，本插件**不提供**（见 [已知限制](docs/known-limits.md)）。

---

## 3. 五条子命令

```powershell
python historica.py init     --out runs/demo          # 初始化运行目录（config_used.json + cache/data/out）
python historica.py estimate --bbox ... --year ...    # 选景 + 并发测速 + 算账（不下载）
python historica.py fetch    --plan runs\<case>\estimate\<ts>\plan.json   # 按同一份计划窗口下载并拼接
python historica.py compose  --run  runs\<case>\estimate\<ts>             # 色调统一 + 配准校验 + 出图
python historica.py report   --run  runs\<case>\estimate\<ts>             # metadata.csv + 技术报告 + 实测回写
```

`python historica.py --version` 打印版本号。

---

## 4. 四种范围输入（各给一个示例）

| 输入 | 示例 | 说明 |
|---|---|---|
| 行政区名 | `--admin "南平市延平区"`<br>`--admin "广东省深圳市"` | DataV.GeoAtlas 按「省 > 市 > 区」链匹配；失败回退 GADM 4.1。来源与精度写进 `plan.json` 与报告 |
| 经纬度 bbox | `--bbox 113.55,22.45,114.55,23.26` | 顺序为 W,S,E,N（度） |
| 矢量文件 | `--vector data/study.geojson`<br>`--vector data/study.zip`（内含 .shp）<br>`--vector data/study.kml` | 只取面要素，按 WGS84 解释；多面自动取并集；路径按当前工作目录解析 |
| 网页框选 | 浏览器打开 `scripts/picker.html`，画框后复制 bbox / GeoJSON / WKT | 纯前端工具；把导出值贴到 `--bbox` / `--vector` / `--wkt` |

WKT 也可直接传：`--wkt "POLYGON((...))"`。

---

## 5. 年份输入

```powershell
python historica.py estimate --bbox ... --year 1993,2003,2012,2024   # 指定年份
python historica.py estimate --bbox ... --range 1990-2025 --step 5  # 区间 + 步长
python historica.py estimate --bbox ... --year 2003 --window 1      # 目标年 ±1 年内自动择优
python historica.py estimate --bbox ... --year 1993 --prefer-date 1993-12-26  # 强制某日
```

默认 `--window 1`：在目标年 ±1 年内按「覆盖 40% / 云量 25% / 太阳高度 15% / 日期离散 20% / 季节一致 15%」打分。
**图上与报告里写的永远是实际成像日期**，不是目标年。

### 5.1 季节锁（v1.1 默认行为）

多期默认在**同一个季节窗口**内选景，`--season-window` 控制半宽（天），默认 45：

```powershell
python historica.py estimate --admin "贵阳市花溪区" --year 1993,2003,2012,2024   # 默认 ±45 天
python historica.py estimate ... --season-window 30                            # 收紧到 ±30 天
python historica.py estimate ... --season-window 0                             # 关闭：回到 v1.0，允许跨季
```

- 窗口中心由**各期初选日期的圆均值**决定，不依赖外部气候分区，任何地区都可复现。
- 窗口内没有可用低云景（或最优覆盖低于 60%）时，**不会静默放开窗口**：回退到窗口外并标记
  `cross_season: true` + 跨季天数，同时给出「放开窗口能换回多少可读覆盖」（用同一次 STAC 检索的候选估算，不下载）。
- 报告第 1.1 节固定输出：改前/改后日序、月份、年内日序标准差、最大差、跨季标记。

**注意：默认行为已改变——不加 `--season-window 0` 就不会跨季。** 好处是四期可读、色调可比；
代价是当年窗口内没有低云景时，只能选同季但云量略高的那一景（覆盖会差一点）。

---

## 6. `--case` 是干什么的

`--case <name>` 只做一件事：把 `config/cases/<name>.json` 里的 `defaults` 当作 argparse 默认值。

- 命令行显式参数始终优先，例如 `--case prd_core --bbox 113.0,...` 会覆盖案例里的 bbox；
- 换范围/换年份不需要改代码，新增一个 case 文件即可；
- 现有案例：`yanping`（延平区，强制 1993-12-26）、`prd_core`（广深莞核心）、`prd_full`（珠三角全量，只出 estimate）、`guizhou_huaxi`（贵阳花溪，第三地形验证）。

---

## 7. estimate 与 fetch 的区别

| | estimate | fetch |
|---|---|---|
| 是否联网下载影像 | 只做 4 路×5 轮 Range GET 探针（约 34 MB/轮） | 按窗口下载全部波段 |
| 是否写 `plan.json` | 写 | 读同一份 `plan.json` |
| 产出 | 选景排名、逐期像素/流量、耗时区间 | `data/epoch_<年>.npz`、`actual.json`、`metadata.csv` |
| 会不会决定“继续跑” | **不会**。预测只用于提醒/是否分批 | 运行期看门狗看实测，连续 3 块低于预估 p5 的 1/2 才暂停 |

预估区间是 **p5–p95**（v1.1 起），样本池 = 本轮并发探针 + 最近 5 次校准净吞吐 + 这些运行在下载期看门狗记录的分块吞吐。

`fetch --plan` 保证下载窗口与预估口径一致；两种都带缓存与断点续传。

---

## 8. 缓存与续传

- 缓存键：`runs/<case>/cache/raw/<item_id>/<band>_<sha1(窗口边界+EPSG)[:16]>.npz`
- 每读完一个波段立即落盘；中断后原样重跑 `fetch`，已下载的波段直接命中缓存。
- 换分辨率、换范围会生成新键，不会误用旧缓存。
- 看门狗触发时（`exit 3`）会先落盘当前波段、写 `fetch_progress.json` 与已完成的期，再退出；续跑不会重下已完成的期。
- 只想重建个别年份：`fetch --plan ... --only-year 1993,2024`（逗号分隔，其余期命中缓存）。
- 预估/实测的净吞吐回写 `cache/calibration.json`，下次 estimate 会把它并入样本（最近 5 条，等权）；
  下载期看门狗的分块吞吐也一并入库（每条运行最多等距抽 10 个），用于修正「开工前测速 ≠ 实际下载」的偏差。

---

## 9. 退出码

| 码 | 含义 | 你要做什么 |
|---|---|---|
| `0` | 成功 | — |
| `2` | 参数错误（范围解析失败、年份非法） | 看 stderr 的“参数错误”，修参数后重跑 |
| `3` | 运行期看门狗触发，任务暂停 | 网络恢复后原样重跑 `fetch`（同一 `--plan`），会续传 |

其他非零码视为异常，请附 `fetch_progress.json` 反馈。

---

## 10. 三个真实案例（复现命令；成品不随包分发）

| 案例 | 复现命令 | 说明 |
|---|---|---|
| 南平市延平区（闽北山地） | `--case yanping leap estimate` | 1993 跨轨缝缺角同季；2003 薄卷云扣 310 km² |
| 珠三角核心（广深莞） | `--case prd_core estimate` → `fetch` → `compose` → `report` | 一次看门狗暂停后续跑；2012 可读 78.8% |
| 贵阳花溪 v1.0（无季节约束） | `--case guizhou_huaxi estimate --season-window 0` | 四期可读 97.8–99.4%；日期跨 2/9/10 月，std 101 天 |
| 贵阳花溪 v1.1（同季约束） | `--case guizhou_huaxi estimate`（默认 ±45 天） | 四期锁进 10–11 月，std 16 天；可读降 2.5 pp（平均） |

运行后成品在 `runs/<case>/estimate/<时间戳>/out/`：`series.jpg`、`single_<年>.jpg`、`metadata.csv`、`technical_report.md`。
第三地形验证回答了“无干季地区选景会不会乱季、湿润区可读能到多少”两个问题；v1.1 加季节锁后的对照与代价，见 [validation-3rd-terrain.md](docs/validation-3rd-terrain.md) 第 6 节。

> 本交付包**不含 `runs/`**（原始 runs 约 1.6 GB，主要是 `cache/raw` 的 npz）。表里的数字来自封版前的实测运行，已写进 `docs/validation-3rd-terrain.md` 与 CHANGELOG，可直接引用；要看到图，按表中命令自己跑一遍。

---

## 11. 已知限制

SLC-off 楔形带、薄卷云 QA 漏判、GADM 缺县级、GCJ-02 转换误差、30 m 判读下限、跨传感器辐射差、跨季与满覆盖不可兼得、配准未锁定时的处理、云掩膜边界不羽化、OCR/年份归属不确定——逐条写在 [docs/known-limits.md](docs/known-limits.md)。
预估与看门狗的设计与三轮实测数字（延平 / 珠三角 / 花溪 v1.0–v1.1）见 [docs/throughput-and-gates.md](docs/throughput-and-gates.md)，范围选景规则见 [docs/extent-selection-design.md](docs/extent-selection-design.md)。

## 12. 数据许可

Landsat C2 L2：USGS/NASA，公有领域，需署名 USGS。Sentinel-2 L2A：Copernicus，需署名 ESA（默认关闭）。行政边界：DataV.GeoAtlas（示意边界）、GADM 4.1（学术用途）。
