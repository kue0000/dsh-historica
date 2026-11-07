---
name: historica
description: 国内任意地区历史卫星影像下载与对比。输入行政区名 / 经纬度 bbox / 矢量文件 / 网页框选范围与年份，自动选景、按真实带宽预估流量与耗时、窗口化下载 Landsat（可选 Sentinel-2）COG、统一色调拼接并出多期对比图与技术报告。用户提到历史影像、卫星影像对比、Landsat、多期影像、城市扩张对比、老影像下载时使用。
---

# historica：历史卫星影像下载与对比

## 概览

把「查景 → 选景 → 估时 → 窗口下载 → 色调统一 → 多期拼图 → 技术报告」做成一条 CLI 流水线。
数据默认来自 Microsoft Planetary Computer 上的 **Landsat Collection 2 Level-2**（免费、公有领域）。
纯 HTTP + COG 窗口读取，不下载整景；带本地缓存与断点续传。

## 适用 / 不适用

适用：
- 县级及以上行政区的多期（1990s→今）影像对比；
- 任意经纬度 bbox / GeoJSON / Shapefile / KML / WKT 范围；
- 需要「先看账再下载」的场景：`estimate --dry-run` 给场景数、窗口像素、流量、耗时区间。

不适用（**必须如实告知用户**）：
- **1982 年以前没有 Landsat 影像**（Landsat 1 于 1972 发射，但 MSS 4 景/年、60 m，且国内早期覆盖稀疏；本插件只承诺 1984 年后的 TM/ETM+/OLI 可用性，1982 年前一律回答「无」）。
- **不提供扫描老地形图**。本插件只处理卫星影像，不接入任何纸质地图扫描件。
- **不保证跨 100 km 的行政边界能在一景内完整**：WRS-2 单景约 170×183 km，跨轨缝（相邻 path 仅重叠约 7%）时必然要多景、多日期。
- **30 m 分辨率不适合判读地块/宅基地边界**，只能看城市建成区、道路、水库等宏观地物。

## 诚实条款（写进任何交付物，不得删改）

1. **跨 WRS-2 轨缝且目标年代无同日低云双轨时**，只有两种次优解：
   - 「缺角同季」：单轨/单日，覆盖不足，但季节一致；
   - 「满覆盖跨季」：多日拼接补满，但拼接缝两侧物候、水位、太阳高度不同。
   **任何声称「既满覆盖又同季」的输出都是拼出来的假完整**——主图必须显式标出缺失区与缺失面积，备选方案只能进「候选排名」，不能悄悄补进主图。
2. **跨传感器辐射差（LEDAPS vs LaSRC）、BRDF、大气未做校正**：1993 的 TM L2 与 2025 的 OLI L2 反射率产品代次不同，色调管线只做显示级统一，**只能用于展示，不能用于变化检测/定量反演**。
3. **绝对定位误差**：Landsat C2 单景约 12 m RMSE（官方几何精度），行政区边界本身为示意（DataV 为 GCJ-02 反算，GADM 为百米级），图面红线与真实权属界可差百米级。**共同偏移无法用相对配准排除**——配准只能证明「两期相对一致」，不能证明「绝对位置正确」。
4. **不伪造数据**：缺失区留空并标注面积；预估与实测误差如实写进报告；超过 30% 要解释原因。

## 安装（Windows / Trae）

包内不含任何本机绝对路径；解压目录即插件根，所有命令在插件根内执行。

```powershell
# 1) 进入插件根（解压所得目录）
cd <插件根>
# 2) 建环境（首次）
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install numpy shapely pyproj matplotlib pyshp pystac-client rasterio
```

路径解析规则：`hist/config.py` 用 `ROOT = dirname(dirname(__file__))` 推导插件根；`--out` / `cache` / 案例 `out` 均为相对路径；唯一可覆盖的路径项是 `estimate.calibration.path`（默认 `cache/calibration.json`，相对插件根）。

依赖均可选：没有 rasterio 时自动走纯 HTTP COG 读取（`hist/coghttp.py`）；没有 pystac-client 时自动走裸 `/search`。

## 快速开始

```powershell
# 0) 初始化（写 config_used.json，建 cache/data/out）
.\.venv\Scripts\python.exe historica.py init --out runs/demo

# 1) 预估（不下载，含 4 路 × 5 轮 8 MB Range GET 探针）
.\.venv\Scripts\python.exe historica.py estimate --case yanping --dry-run
.\.venv\Scripts\python.exe historica.py estimate --case prd_core --dry-run

# 2) 确认预估后再下载
.\.venv\Scripts\python.exe historica.py fetch --plan runs\yanping\estimate\<时间戳>\plan.json

# 3) 色调 + 配准校验 + 出图
.\.venv\Scripts\python.exe historica.py compose --run runs\yanping\estimate\<时间戳>

# 4) 元数据 CSV + 技术报告
.\.venv\Scripts\python.exe historica.py report --run runs\yanping\estimate\<时间戳>
```

`--case` 只是把 `config/cases/<name>.json` 里的 `defaults` 当 argparse 默认值，命令行显式参数仍然优先。改名/换范围不需要改代码。

## 范围（四选一）

| 输入 | 写法 | 说明 |
|---|---|---|
| 行政区 | `--admin "南平市延平区"` / `--admin "广东省深圳市"` | DataV.GeoAtlas 全量索引按「省>市>区」链匹配，GCJ-02→WGS84 反算；失败回退 GADM 4.1 |
| bbox | `--bbox 113.55,22.45,114.55,23.26` | W,S,E,N，单位度 |
| 矢量 | `--vector a.geojson` / `.shp` / `.zip` / `.kml` | 只取面要素，假定 WGS84 |
| 网页框选 | 浏览器打开 `scripts/picker.html`，画框后复制 bbox/GeoJSON | 纯前端，无网络依赖（底图瓦片除外） |

边界来源与精度会写进 `plan.json` 与报告第 1 节；不写「约」「大概」这类模糊词。

## 年份（三种）

- 指定：`--year 1993,2004,2013,2025`
- 区间+步长：`--range 1990-2025 --step 5`
- 自动择优：`--window 1` 在目标年 ±1 内按五项打分选候选，并输出排名（默认即开启）。

强制某年用某日：`--prefer-date 1993-12-26`（可重复，也接受 `1993-12-26` 或 `1993=1993-12-26`）。

## 选景打分（五项，权重在 `config/default.json`）

| 项 | 权重 | 口径 |
|---|---|---|
| 覆盖度 | 0.40 | 同日/同组足迹并集 ∩ 目标面 面积比 |
| 云量 | 0.25 | `1 - eo:cloud_cover/100` |
| 太阳高度 | 0.15 | `1 - |sun_elev - 45°|/90°` |
| 日期离散 | 0.20 | 目标年 ±window 内的距离衰减 |
| **季节一致** | **0.15** | 相对共同窗口中心：中心 1.0、偏移 = 半宽 0.5、≥2× 半宽 0 |

流程：STAC 查询 → 按日期分组（同日多轨合并）→ 足迹并集 → 打分排序 → 若单日覆盖 < 目标覆盖且跨日期能多补 ≥5 个百分点，选混合方案（记录 `span_days` 季节代价）；每期最多 `max_scenes_per_epoch` 景。
前 6 名全部写入报告的「候选排名」，包括被放弃的「缺角同季 / 满覆盖跨季」方案。

## 季节锁（v1.1 起默认开启）

多期影像默认必须落在同一个季节窗口内，避免「秋/冬/秋/秋」：

1. 先做一遍无约束初选，得到各期日期；
2. 用这些日期的**年内日序圆均值**定出共同窗口中心，窗口 = 中心 ±`--season-window` 天（默认 `config/default.json` → `selection.season_window_days = 45`）；
3. 在窗口内重新选景，打分含季节一致性项。

```powershell
# 默认：±45 天同季
python historica.py --case guizhou_huaxi estimate

# 自定义窗口
python historica.py --case guizhou_huaxi estimate --season-window 30

# 关闭季节锁（允许跨季，v1.0 行为）
python historica.py --case guizhou_huaxi estimate --season-window 0
```

**默认行为已改变：不加 `--season-window 0` 就不会跨季。**

窗口内没有可用低云景时**不会静默放开窗口**：

- plan.json 写入 `season.cross_season => true`、`cross_season_days`、`fallback_reason`；
- 报告 1.1 节报出「降级后的实际日期与可读覆盖」；
- 同时给出「如果放开窗口，可读覆盖能换回多少」（用同一次 STAC 检索的候选算，不下载，标注为推断值）。

报告 1.1 节还会输出四期的年内日序、月份、标准差与最大差（v1.1 花溪实测：标准差 101.2 → 16.2 天，最大差 250 → 42 天）。

## 预估口径（不下载也能算账）

1. **并发探针**：与 `fetch.workers` 同数（默认 4）同时发 8 MB Range GET，跑 5 轮，每轮按「总字节 / 墙钟」聚合，取 5 轮中位。单流探针只作诊断字段，不进估算。
2. 窗口像素 = 目标 bbox ∩ 场景足迹的 bbox → 场景原生 CRS → 裁到影像范围 → 按 256 px 块对齐（与 `read_band` 完全相同）。
3. 原始体积 = 窗口px ×（4 光谱 + 1 薄卷云筛查）× 2 字节 × 1.25（块放大）；传输体积 = 原始 × **实测压缩比**（COG 全文件字节数 / 未压缩字节数，`Content-Range` 取，不下载）。
4. 耗时 = 传输/吞吐 + 逐期打开延迟 + 处理时间。**点估计 = 样本中位，区间 = p5–p95**（v1.1 起；v1.0 为 p10–p90）；样本 = 本轮探针轮次 + 最近 5 条校准净吞吐 + 这些运行在下载期看门狗记录的分块吞吐（每条等距最多抽 10 个），等权；样本 < 4 时回退 `time_range` 系数并标注。
5. 预测只用于提醒/是否分批，**不决定任务是否继续**。运行期看门狗监控实测：连续 3 个读取块 < 预估区间低端（p5）的 1/2 → 先落盘、再暂停、退出码 3、可续跑。
6. 实测后写 `actual.json`，与预估逐期对比；支持分段（暂停/续跑）、冷启动等价；误差 > 30% 时报告必须解释。

## 缓存与续传

- 键：`<cache>/raw/<item_id>/<band>_<sha1(窗口边界+EPSG)[:16]>.npz`；
- 命中即跳过下载；每波段读完立即落盘，中断后重跑只补缺；
- 换分辨率/换范围会自然产生新键，不会误用旧缓存。
- 看门狗触发（退出码 3）时会先落盘当前波段，再写 `fetch_progress.json` 与已完成的期；原样重跑即可续传。
- 只想重建个别年份时用 `fetch --only-year 1993,2024`（逗号分隔，其余期直接命中缓存）。
- 实测净吞吐回写 `cache/calibration.json`，下次 estimate 并入样本（最近 5 条，等权）；同时写入本轮看门狗分块吞吐 `block_speeds_mbps`，下一轮估算时等距抽取进样本池。

## 输出

```
runs/<case>/estimate/<ts>/  plan.json  estimate.json  config_used.json
                            actual.json  align.json  fetch_progress.json（fetch 后）
runs/<case>/.../data/       epoch_<year>.npz（rgb/mask/valid_frac）
runs/<case>/.../out/        series.jpg   single_<year>.jpg   metadata.csv   technical_report.md
```

- 组图：自动 N×M、红色虚线行政界、统一比例尺与指北针、缺失区斜纹+「缺测 X km²」标注；
- 元数据 CSV：目标年/景 ID/传感器/日期/云量/太阳高度/场景 EPSG/原生分辨率/AOI bbox/覆盖率，以及 v1.1 新增的 `day_of_year` / `season_window_days` / `season_center_doy` / `season_offset_days` / `cross_season`；
- 技术报告：范围与边界来源、**1.1 季节一致性**、候选排名、处理参数、预估 vs 实测、配准校验、偏差与限制、复现命令、数据许可。

## 色调与配准（移植自 v1，未重写）

- 色调：DOS p1.5 → 白平衡 (1, 0.775, 0.61) → 固定拉伸 0.004–0.36、gamma 0.80 → p10/p90 锚定 (0.055, 0.47) → 对比 1.06 → 饱和 1.08 → 目标均值 0.285；
- 配准：梯度幅值 NCC，±12 px，3 窗口 × 相邻期，置信比 =(峰值 − p90)/峰值；FFT 互相关。

## 最可能失败的场景（一句话）

**网络吞吐在下载时段骤降 + 跨轨缝年份又碰上多云，导致「预估 20 分钟」变成 1 小时以上；而 v1.1 默认同季窗口又排除了窗口外的低云景，最终图面不得不在「缺角同季 / 满覆盖跨季」二选一。**

- v1.1 起默认不跨季：若窗口内只有 60–70% 覆盖的候选，报告会明确提示实际日期、`cross_season` 状态与放开窗口的推断收益；需要跨季抢低云景时显式加 `--season-window 0`。

## 数据许可

- Landsat C2 L2：USGS/NASA，公有领域，需署名 USGS；
- Sentinel-2 L2A：Copernicus，开放许可，需署名 ESA（config 默认关闭，2015 年后可开）；
- 天地图 / 高分：需 key/授权与配额，本插件**默认不纳入**，config 仅预留 provider 接口；
- 行政边界：DataV.GeoAtlas（示意，非测绘成果）、GADM 4.1（学术用途，百米级）。
