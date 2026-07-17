## Twitter/X Wayback 存档工具 · archive.py

将公开 Twitter/X 账号的历史推文通过 Wayback Machine 完整存档至本地，支持 HTML、图片、视频、头像的批量抓取与增量更新，并生成可供 [IncandescenceReader](https://github.com/sjshb57/IncandescenceReader) 直接读取的索引文件。

> 此项目为 [白炽阅读器 · IncandescenceReader](https://github.com/sjshb57/IncandescenceReader) 的配套存档脚本。  
> 每个人的文字都值得被留存。

---

### 附注

> Twitter存档组织：[TwitterArchiver](https://github.com/TwitterArchiver)已成立
> 
> 如果您想在此留档，请在[此处提交申请](https://twitterarchiver.github.io/home/guestbook.html)

---

## 目录结构

```
accounts/
  {USERNAME}/
    cdx_data.json             ← fetch-cdx 生成，Wayback CDX 快照清单
    archive.py                ← 本脚本（或从上级目录引用）
    wayback_snapshots/
      html/                   ← 下载的 Wayback HTML 快照
      json/                   ← 从 HTML 提取的推文原始 JSON
      image/                  ← 下载的图片
      video/                  ← 下载的视频
      avatar/                 ← 下载的头像
      index.json              ← build-index 生成，供前端读取
      profile.json            ← 账号信息（需手动维护）
      _log/                   ← 状态系统目录（自动生成）
        archive_index.json    ← 主账本，记录每个 URL 的下载状态
        html_done.txt
        html_failed.txt
        html_failed_all.txt
        image_done.txt
        image_failed.txt
        image_failed_all.txt
        video_*.txt
        avatar_*.txt
        media_*.txt
```

---

## 安装

需要 Python 3.10+。

```bash
pip install requests beautifulsoup4 lxml
```

---

## 使用方法

### Wayback Machine 流程（公开账号）

在账号目录（`accounts/{USERNAME}/`）下依次执行：

```bash
# 第一步：抓取 Wayback CDX 快照清单
python ../../archive.py fetch-cdx {USERNAME}

# 第二步：下载 Wayback HTML 快照（同时提取推文 JSON）
python ../../archive.py fetch-html

# 第三步：下载图片、视频、头像
python ../../archive.py fetch-media

# 第四步：清洗 HTML 路径
python ../../archive.py clean-html

# 第五步：生成前端索引
python ../../archive.py build-index
```

完成后将 `wayback_snapshots/` 目录提供给 IncandescenceReader 即可。

---

### 私密账号流程（download_archive.py 的 dump）

适用于 Wayback 上没有存档的账号（私密/锁推），数据来自 `download_archive.py` 抓取的 dump：

```bash
# 在账号目录下
python ../../archive.py convert /path/to/dump_dir/

# 从 json/ 渲染出 HTML（伪 wayback 风格，供 build-index 使用）
python ../../archive.py render-html

# 建索引
python ../../archive.py build-index
```

dump 目录需要有这样的结构：

```
dump_dir/
  {USER}_archive_assets/
    snapshots.json      # {user, snapshots:[{timestamp, original_url, json_filename}]}
    media_index.json    # 原始 URL → 本地 hash 文件的映射
    json/{ts}_{tid}.json
    media/{hash}.{ext}
```

> `convert` 接收的是 **download_archive.py 的 dump**，不是 X 官方的数据导出 ZIP。
> 找不到 `snapshots.json` 会直接报错退出。

---

### 增量更新

直接再次执行相同命令即可，脚本会自动跳过已成功下载的内容，只处理新增和失败的部分：

```bash
python ../../archive.py fetch-cdx {USERNAME}
python ../../archive.py fetch-html
python ../../archive.py fetch-media
python ../../archive.py clean-html
python ../../archive.py build-index
```

---

## 子命令详解

### `fetch-cdx`

```bash
python archive.py fetch-cdx {USERNAME} [--from YYYYMMDD] [--to YYYYMMDD] [--no-collapse-digest]
```

查询 Wayback Machine CDX API，获取账号所有推文快照的清单，写入 `cdx_data.json`。

| 参数 | 说明 |
|---|---|
| `--from YYYYMMDD` | 只抓指定日期之后的快照 |
| `--to YYYYMMDD` | 只抓指定日期之前的快照 |
| `--no-collapse-digest` | 不对相同内容的快照去重（默认去重）|

---

### `fetch-html`

```bash
python archive.py fetch-html [--workers N] [--delay S] [--force]
```

读取 `cdx_data.json`，逐条下载 Wayback HTML 快照，同时从 HTML 中提取推文原始 JSON 存入 `json/`。

| 参数 | 说明 |
|---|---|
| `--workers N` | 并发线程数（默认 7）|
| `--delay S` | 每次请求间隔秒数（默认 0.8）|
| `--force` | 强制重新下载已完成的项 |

---

### `fetch-media`

```bash
python archive.py fetch-media [--workers N] [--delay S] [--force]
```

扫描 `json/` 下所有推文 JSON，下载引用的图片和视频到 `image/` 和 `video/`，同时下载头像到 `avatar/`。

---

### `fetch-avatars`

```bash
python archive.py fetch-avatars [--workers N] [--force]
```

单独补全头像下载。扫描所有推文 JSON，对本地缺失的头像重新下载。

---

### `clean-html`

```bash
python archive.py clean-html
python archive.py clean-html --force
```

清洗 HTML，将媒体路径替换为本地路径：
- **头像**：直接由 pid 构造 `../avatar/avatar_{pid}.{ext}`，唯一确定
- **图片**：本地有则用真实文件名，没有则构造预期文件名（以后 retry 下到自动显示）
- **视频**：本地有则替换，没有则删除 video 标签
- **删除** Wayback Machine 工具栏和 JSON 展示元素

已清洗的 HTML 自动跳过，增量跑只处理新增的。

| 参数 | 说明 |
|---|---|
| `--force` | 强制重新清洗所有文件 |

---

### `build-index`

```bash
python archive.py build-index
```

扫描 `html/` 和 `json/` 目录，结合本地媒体文件，生成 `index.json` 供前端读取。

每条记录包含：

```json
{
  "file": "20240705_twitter_com_username_status_xxx.html",
  "timestamp": "2024-07-05T14:51:29.000Z",
  "date": "2024-07-05",
  "text": "推文正文预览...",
  "tweet_id": "...",
  "author_name": "...",
  "author_username": "@...",
  "author_avatar": "../avatar/avatar_xxx.jpg",
  "wanted_images": ["../image/xxx.jpg"],
  "wanted_videos": ["../video/xxx.mp4"],
  "wanted_avatars": ["../avatar/avatar_xxx.jpg"],
  "embedded_images": [...],
  "embedded_videos": [...],
  "is_reply": false,
  "is_virtual": false
}
```

---

### `retry`（重试失败项）

```bash
python archive.py retry --image-failed
python archive.py retry --image-failed-all
python archive.py retry --video-failed
python archive.py retry --video-failed-all
python archive.py retry --avatar-failed
python archive.py retry --avatar-failed-all
python archive.py retry --html-failed
python archive.py retry --media-failed
```

从对应的 `_log/*.txt` 文件读取失败列表并重新尝试。

| 后缀 | 说明 |
|---|---|
| `*-failed` | 可救失败（网络超时、限速等），每次增量自动重试 |
| `*-failed-all` | 永久失败（SSL 错误 = Wayback 未存档、HTTP 4xx），需手动触发 |

---

### `rebuild-index`

```bash
python archive.py rebuild-index
```

从 `_log/*.txt` 和本地媒体文件重建 `archive_index.json` 状态账本。在状态文件损坏或迁移旧版本后使用。

---

### `dedup`

```bash
python archive.py dedup [--execute]
```

检测重复媒体文件（相同内容不同文件名）和孤儿文件（有文件但无 JSON 引用）。默认 dry-run，加 `--execute` 实际删除。

---

### `convert`

```bash
python archive.py convert /path/to/dump_dir/
```

将 `download_archive.py` 产出的 dump 转换为本项目格式，写入 `json/` 与 `image/` `video/` `avatar/`。
需要 dump 内含 `snapshots.json` 与 `media_index.json`，否则报错退出。转换后接 `render-html` 生成 HTML。

---

## 状态系统

脚本使用 `_log/` 目录追踪每个 URL 的下载状态，支持断点续传和精确重试。

| 状态 | 说明 | 对应文件 |
|---|---|---|
| `done` | 下载成功 | `*_done.txt` |
| `failed` | 可救失败（超时/限速）| `*_failed.txt` |
| `failed_all` | 永久失败（未存档/4xx）| `*_failed_all.txt` |

- **SSL 错误**：Wayback Machine 对未存档资源返回 SSL 握手失败，归为永久失败，不再重试
- **ConnectTimeout**：TCP 连接超时，通常是 Wayback 限速，归为可救失败，下次增量自动重试
- **HTTP 4xx**（除 408/429）：归为永久失败

---

## GitHub Actions 自动化

提供两个 workflow 文件，fork 对应账号的仓库后开箱即用：

### `setup.yml` — 首次建档

手动触发（`workflow_dispatch`），分两阶段：
1. **第一阶段**：`fetch-html` → `build-index` → 推送（纯文字版，快速上线 Pages）
2. **第二阶段**：`fetch-media` → retry × 3 → `clean-html` → `build-index` → 推送

### `update.yml` — 每周增量

每周日 UTC 02:00（北京时间周日 10:00）自动触发，也可手动触发：

```
fetch-cdx → fetch-html → fetch-media → retry(failed) → clean-html → build-index → push
```

### `retry_all.yml` — 全量重试（手动触发）

专门重试 `failed_all`（永久失败）的媒体文件，适用于 Wayback Machine 补存了之前未存档的内容：

```
retry --image-failed-all / --video-failed-all / --avatar-failed-all
→ clean-html → build-index → push
```

**仓库名即用户名**：workflow 自动从仓库名读取 Twitter/X 用户名，无需任何配置。

---

## 参数调优

修改脚本顶部的常量来调整行为：

```python
# 本地使用（默认）
REQUEST_ATTEMPTS   = 5      # 重试次数
MAX_BACKOFF        = 60.0  # 最大退避等待秒数
MEDIA_TIMEOUT_IMAGE  = (5, 40)   # (connect超时, read超时)
WAYBACK_HTML_TIMEOUT = (5, 60)
DEFAULT_WORKERS_HTML   = 7
DEFAULT_WORKERS_MEDIA  = 8
DEFAULT_WORKERS_AVATAR = 4
DEFAULT_DELAY_HTML     = 0.8
DEFAULT_DELAY_MEDIA    = 0.3

# GitHub Actions 推荐（数据中心网络，IP 每次不同）
REQUEST_ATTEMPTS   = 1
MAX_BACKOFF        = 20.0
MEDIA_TIMEOUT_IMAGE  = (4, 8)
DEFAULT_WORKERS_HTML   = 30
DEFAULT_WORKERS_MEDIA  = 40
DEFAULT_WORKERS_AVATAR = 30
DEFAULT_DELAY_HTML     = 0.15
DEFAULT_DELAY_MEDIA    = 0.08
```

---

## License

Copyright © 2026 sjshb57

本项目基于 [GNU Affero General Public License v3.0](https://www.gnu.org/licenses/agpl-3.0.html) 开源。你可以自由使用、修改和分发本项目，但衍生作品必须同样以 AGPL-3.0 协议开源。

---

## 赞助

<p align="center">
  <img src="https://free.picui.cn/free/2026/06/24/6a3b25866f0fd.jpg" width="100%" alt="赞助图片">
</p>
