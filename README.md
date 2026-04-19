# NASA APOD Downloader

批量下载 [NASA 每日天文图（APOD）](https://apod.nasa.gov/apod/astropix.html) 的高清图片与元数据，并生成可本地浏览的静态 HTML 图库。

---

## 功能

- 按日期范围、最近 N 天或指定日期批量下载高清图片
- 每张图片生成 **JSON** 和 **Markdown** 双格式元数据文件
- **SQLite 数据库**索引全部元数据，方便查询统计
- 自动跳过已下载内容，支持断点续跑
- 视频条目自动保存缩略图，无缩略图时优雅跳过
- 异步并发下载，可配置并发数与请求间隔
- 一键生成**静态 HTML 图库**，支持年份筛选、标题搜索、大图预览

---

## 快速开始

**环境要求：** Python 3.10+

```bash
# 1. 克隆项目
git clone <repo-url> && cd nasa

# 2. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置 API Key（可选，不配置使用 DEMO_KEY 限额较低）
cp .env.example .env
# 编辑 .env，填入 NASA_API_KEY
# 免费申请：https://api.nasa.gov/
```

---

## 使用方法

```bash
# 下载今天
python downloader.py --today

# 下载指定日期
python downloader.py --date 2024-04-16

# 下载最近 30 天
python downloader.py --latest 30

# 下载日期范围
python downloader.py --start 2024-01-01 --end 2024-12-31

# 下载全部历史（1995-06-16 至今，约 11000 张，50-100 GB）
python downloader.py --start 1995-06-16

# 强制重新下载（忽略已有文件）
python downloader.py --latest 30 --force

# 生成 HTML 图库
python downloader.py --gallery

# 为已有记录批量补生成 Markdown 文件
python downloader.py --rebuild-markdown
```

---

## 图库预览

运行 `--gallery` 后，用浏览器打开 `data/gallery.html`：

- 深色主题响应式网格
- 按年份一键筛选
- 标题实时搜索
- 点击卡片弹出大图、完整说明及 NASA 原页链接

每次下载新数据后重跑一次 `--gallery` 即可更新图库。

---

## 存储结构

```
data/
├── apod.db              ← SQLite 元数据索引
├── gallery.html         ← 静态 HTML 图库
└── images/
    └── YYYY/
        └── MM/
            ├── YYYY-MM-DD.jpg    ← 高清图片
            ├── YYYY-MM-DD.json   ← JSON 元数据
            └── YYYY-MM-DD.md     ← Markdown 元数据
```

### 元数据字段

| 字段 | 说明 |
|------|------|
| `date` | 日期（YYYY-MM-DD） |
| `title` | 图片标题 |
| `explanation` | 天文学家撰写的详细说明 |
| `media_type` | `image` 或 `video` |
| `url` | 标准分辨率链接 |
| `hdurl` | 高清图片链接 |
| `nasa_page` | NASA APOD 官方页面链接 |
| `copyright` | 版权归属（公共领域时为空） |
| `local_path` | 本地图片文件相对路径 |

---

## 数据库查询示例

```bash
sqlite3 data/apod.db
```

```sql
-- 已下载图片列表（最新在前）
SELECT date, title FROM apod WHERE downloaded = 1 ORDER BY date DESC;

-- 公共领域图片（无版权限制）
SELECT date, title FROM apod WHERE copyright = '' AND downloaded = 1;

-- 按年统计数量
SELECT substr(date, 1, 4) AS year, COUNT(*) AS count FROM apod GROUP BY year;

-- 查找关键词
SELECT date, title FROM apod WHERE title LIKE '%nebula%';
```

---

## API 速率限制

| Key 类型 | 限额 |
|----------|------|
| `DEMO_KEY` | 30 次/小时 |
| 个人 API Key | 1000 次/小时 |

下载器默认每次请求间隔 0.3 秒、5 个并发，在两种 Key 下均安全运行。

---

## 注意事项

- APOD 于 **1995-06-16** 正式上线，更早的日期会自动修正
- 全量历史约 11000 张图，磁盘占用 50-100 GB，建议先用 `--latest 30` 试跑
- 部分条目为 YouTube 视频，有缩略图则保存缩略图，否则仅记录元数据

---

## Roadmap

- [x] 异步批量下载
- [x] JSON + Markdown 双格式元数据
- [x] SQLite 元数据索引
- [x] 断点续传（增量下载）
- [x] 静态 HTML 图库
- [ ] 每日定时同步
- [ ] RSS Feed 生成

---

## License

本项目仅供个人学习使用。
所有 APOD 图片及文字版权归 NASA 及相应摄影师所有，详见 [NASA 媒体使用指南](https://www.nasa.gov/nasa-brand-center/images-and-media/)。
