# 北大法宝 (pkulaw.com) 法律法规智能全量采集系统

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Network](https://img.shields.io/badge/curl__cffi-Chrome%20Fingerprint-orange.svg)](https://github.com/yifeikong/curl_cffi)
[![Parser](https://img.shields.io/badge/parser-lxml%20%2F%20bs4-green.svg)](https://www.crummy.com/software/BeautifulSoup/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

基于**纯 HTTP 协议逆向**的北大法宝（pkulaw.com）法规大数据全量采集与清洗导出系统。彻底摆脱 Selenium / Playwright 等无头浏览器的沉重资源消耗，结合**多维聚合树级联下钻**、**双向排序对穿**与**高频词切片算法**，完美穿透访客检索 200 条硬限制，支持**中央法规库 (`chl`)** 与**地方法规库 (`lar`)** 的几十万级全量无损爬取。

---

## 🌟 核心特性

- **纯协议级极速逆向**：基于官方聚合接口 (`/Aggregate/SingleClassResult`) 与检索接口 (`/law/search/RecordSearch`)，使用 `curl_cffi` 模拟现代 Chrome TLS 指纹与会话上下文，单机支持高并发轻量运行。
- **穿透 200 条检索截断墙**：
  - **多维下钻**：自动按 `发布年份 × 效力位阶 × 省份 × 细分行政机关` 构建级联聚合树。
  - **正反双向对穿**：在单桶数据 $\le 400$ 篇时，使用 `Order=0`（公布日期倒序）与 `Order=1`（正序）双向采集并在内存中通过法宝 ID 聚合，无须任何切片即可无损拿下 400 条以内的数据。
  - **高频词切片机制**：针对特大机关（如生态环境部单年发文 $>800$ 篇、广东等经济大省单年直属发文超千篇）的叶子桶，采用标题高频公文特征词切片探测，100% 覆盖全部历史公文。
- **工业级代理池支持 (`ProxyPool`)**：
  - **隧道代理**：原生适配商业动态隧道代理（如 DataImpulse、亮数据等），支持失败秒级重拨与出口 IP 轮换。
  - **API 提取**：支持各家代理服务商的提取 URL（兼容纯文本及多种 JSON 响应结构）。
  - **代理列表**：支持自定义 `proxies.txt` 本地文件并发测活。
  - **免费源探测**：内置 GitHub 活跃代理源并发探测器，提供开箱即用的测试能力。
  - **风控自愈**：遇到 `IsBlack=true` 或滑块验证码拦截时，自动剔除故障节点并透明重试当前分片。
- **细粒度断点续传 (`CrawlCheckpoint`)**：以叶子节点（如 `lar|年份|效力位阶|省份`）为状态基准，网络中断或手动中止后再次运行秒级跳过已完成项；数据实时流式追加写入 `JSONL`。
- **规范化元数据解析与导出**：
  - 精准提取每篇法规的 **标题、时效性、效力位阶、制定机关、发文字号、公布日期、施行日期、发布年份、法宝ID、详情URL**。
  - 自动输出 **UTF-8-SIG 带 BOM 的 CSV**（Excel 打开绝无中文乱码）与格式化的 **.xlsx** 工作簿。

---

## 🏗️ 架构流程

```mermaid
flowchart TD
    A[启动采集 CLI: main.py] --> B{初始化代理池 ProxyPool}
    B --> C[建立 TLS 指纹 Client / Warmup]
    C --> D[拉取官方聚合树 SingleClassResult]
    D --> E[按 年份 × 效力位阶 × 省份 划分叶子桶]
    E --> F{检查 Checkpoint 状态}
    F -- 已完成 --> G[跳过]
    F -- 未完成 --> H[并发线程池 Worker 提取]
    H --> I{叶子桶数据量}
    I -- "<= 200 条" --> J[单次正序拉取全量]
    I -- "201 ~ 400 条" --> K[倒序 200 + 正序 200 双向对穿]
    I -- "> 400 条" --> L[标题高频词切片穿透]
    J --> M[HTML 解析器提取 6 大核心字段]
    K --> M
    L --> M
    M --> N[流式写入 JSONL 存档]
    N --> O[标记叶子桶完成 Checkpoint]
    O --> P[全量采集结束 Exporter 自动导出 CSV / XLSX]
```

---

## 📁 目录结构

```text
pkulaw_crawler/
├── main.py                 # 主入口：多线程编排、任务分发、CLI 交互
├── pkulaw_client.py        # 核心客户端：TLS 指纹维护、聚合树拉取、检索翻页
├── parser.py               # HTML 结构化解析：提取标题与 6 项子标题属性
├── proxy_pool.py           # 智能代理池：隧道、API、本地文件、GitHub 免费源
├── checkpoint.py           # 线程安全断点续传：JSONL 流式归档与状态恢复
├── exporter.py             # 数据导出器：去重/原样保留、CSV 与 Excel 生成
├── backfill_chl_2019.py    # 专项补采脚本：中央法规 2019 特大桶深度穿透
├── backfill_lar_7provs.py  # 专项补采脚本：地方法规 7 大重点省份大叶子桶穿透
├── verify_provinces.py     # 审计验证脚本：比对本地采集数与官方统计大盘
├── proxies.example.txt     # 自定义代理列表模板
├── requirements.txt        # Python 依赖清单
└── output/                 # 数据输出目录 (自动生成，已加 gitignore)
    ├── pkulaw_chl_env_laws.csv
    ├── pkulaw_chl_env_laws.xlsx
    ├── pkulaw_lar_env_laws.csv
    └── pkulaw_lar_env_laws.xlsx
```

---

## 🚀 快速上手

### 1. 安装环境

推荐使用 Python 3.8 或以上版本：

```bash
git clone https://github.com/zc627788/pkulaw.git
cd pkulaw
pip install -r requirements.txt
```

### 2. 运行模式

#### 模式 A：默认采集全量（先中央法规 `chl`，后地方法规 `lar`）
```bash
python main.py
```

#### 模式 B：单独采集中央法规 / 地方法规
```bash
# 单独跑中央法规 (约 1.29 万条)
python main.py --library chl

# 单独跑地方法规 (约 18.7 万条)
python main.py --library lar
```

#### 模式 C：搭配代理加速（强烈推荐生产使用）
北大法宝对单 IP 请求频率有严格限制，配置高匿住宅隧道代理可将并发提升至 10~20 线程：

```bash
# 接入商业动态隧道代理 (自动轮换出口 IP)
python main.py --library lar --workers 10 --proxy "http://username:password@gateway.example.com:823"

# 或通过环境变量传入代理地址
export PKULAW_PROXY="http://username:password@gateway.example.com:823"
python main.py --library lar --workers 10 --proxy "$PKULAW_PROXY"

# 接入短效代理提取 API
python main.py --library lar --proxy-api "https://api.proxyprovider.com/get?num=20&type=1"

# 接入本地 proxies.txt 文件
python main.py --library lar --proxy-file proxies.txt
```

#### 模式 D：小范围调试与抽样试跑
```bash
# 只采中央法规 2024~2026 年
python main.py --library chl --years 2024,2025,2026

# 只采地方法规 2025 年浙江省的规章
python main.py --library lar --years 2025 --provinces 浙江省 --eff XM07
```

#### 模式 E：仅重新导出表格（不触发网络请求）
如果在已有 `records_chl.jsonl` 或 `records_lar.jsonl` 的基础上需要重新生成 Excel / CSV：
```bash
python main.py --export-only
```

---

## ⚙️ 命令行参数详表

| 参数 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `--library` | `both` | 采集库选择：`chl`（中央法规）、`lar`（地方法规）、`both`（依次采集双库） |
| `--category` | `089` | 分类代码，默认 `089`（环境保护专题） |
| `--workers` | `5` | 并发采集工作线程数（使用稳定隧道代理时建议设为 `10`~`15`） |
| `--years` | `all` | 限制年份，多个逗号分隔，如 `2023,2024` 或 `all`（全量 1949 至今） |
| `--eff` | `all` | 限制效力位阶代码（如 `XA01`, `XM07`），逗号分隔 |
| `--provinces`| `all` | `lar` 专享：限制采集省份，如 `广东省,浙江省` |
| `--proxy` | `""` | 商业动态隧道代理 URL（格式 `http://user:pass@host:port`） |
| `--proxy-api`| `""` | 代理服务商提取 API 接口 |
| `--proxy-file`| `""` | 本地代理 IP 文件路径 |
| `--use-free-proxy` | `False` | 自动接入 GitHub 开源免费代理列表进行测活轮换 |
| `--delay-min`| `0.1` | 单请求基础随机延迟最小值（秒） |
| `--delay-max`| `0.3` | 单请求基础随机延迟最大值（秒） |
| `--force` | `False` | 忽略 Checkpoint 断点，强制从头全量重新跑 |
| `--export-only` | `False` | 仅读取本地 JSONL 重新导出 CSV / XLSX |

---

## 📊 数据字段字典

导出的 `CSV` 与 `XLSX` 包含以下 10 个标准字段：

| 字段名称 | 示例数据 | 字段说明 |
| :--- | :--- | :--- |
| **标题** | 广州市生态环境局关于下达2024年度主要污染物总量控制计划的通知 | 法规公文完整标题 |
| **时效性** | 现行有效 / 已修改 / 废止 | 官方时效标签 |
| **效力位阶** | 地方法规 / 地方规范性文件 / 部委规章 / 行业规范 | 法律效力层级 |
| **制定机关** | 广州市生态环境局 | 公文发布/制定主体 |
| **发文字号** | 穗环〔2024〕12号 | 官方红头文件文号 |
| **公布日期** | 2024-03-15 | 官方印发或公开发布日期 |
| **施行日期** | 2024-03-15 | 正式实施日期 |
| **发布年份** | 2024 | 从公布日期抽取的年份标识 |
| **法宝ID** | `chl123s456` / `lar789a012` | 北大法宝全局唯一公文 GID |
| **详情链接** | `https://www.pkulaw.com/chl/123s456.html` | 在线原文详情直达直链 |

---

## 🔍 针对特大单叶子桶（>400篇）的无损穿透

由于北大法宝前端即使切换到第 20 页（每页 10 条）后也不再向访客提供后续结果，常规正反双向对穿最高仅能拿到 $200 + 200 = 400$ 篇。

本项目提供了专项补全工具：
1. **中央法规补全 (`backfill_chl_2019.py`)**：针对 2019 年生态环境部（871 篇），结合公文类型特征高频词分片下发，将原本只能拿 400 篇的极端桶补全至全量 870+ 篇。
2. **地方法规重点省份补全 (`backfill_lar_7provs.py`)**：自动扫描广东、山东、陕西等发文大省中单年单机关发文 $>400$ 篇的桶，进行自动多词分片补全。
3. **数据一致性稽核 (`verify_provinces.py`)**：直接连线官方实时分类接口，自动将本地落盘数据按 31 个省份逐项进行对账核验。

---

## ⚠️ 免责声明

1. 本项目仅供技术逆向学习、学术研究及数据分析交流使用。
2. 请合理控制采集频率（推荐使用 `--delay-min` / `--delay-max` 进行速率控制），请勿对目标网站服务器造成异常负载。
3. 采集数据版权均归原发文机关及北大法宝所有。不得将本项目用于商业贩售或任何侵犯他人合法权益的场景。
