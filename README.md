# douyin

抖音用户视频批量下载工具，基于 Python + Playwright 实现，模拟浏览器行为，自动抓取用户主页的视频列表并下载保存到本地。

## 功能特性

- 🤖 使用 Playwright 模拟真实浏览器访问（支持无头模式 / 有界面模式）
- 📜 自动滚动页面，加载全部视频列表
- 🔗 拦截 Douyin 内部 API 响应，提取视频下载地址
- ⬇️ 多格式回退策略，优先无水印链接
- ⏭️ 已下载文件自动跳过，支持断点续传
- 🍪 支持登录 Cookie，可访问需登录的内容

## 环境要求

- Python ≥ 3.10
- 网络可访问 `www.douyin.com`

## 安装

```bash
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. 安装 Playwright 所需浏览器（仅首次需要）
playwright install chromium
```

## 使用方法

### 基本用法

```bash
python douyin_scraper.py <用户主页URL>
```

示例：

```bash
python douyin_scraper.py \
  "https://www.douyin.com/user/MS4wLjABAAAAl61SDq2w6mLhMWpv1-ABXqdBRV9nrcyr140Oxf3aPiXE_L0bt5XR15XGm2SajP72"
```

视频默认保存到当前目录的 `downloads/` 文件夹。

### 全部参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `url` | 抖音用户主页链接 | 内置示例用户 |
| `-o / --output-dir` | 视频保存目录 | `downloads` |
| `--no-headless` | 显示浏览器窗口（便于调试或手动登录） | 关闭 |
| `--scroll-pause` | 每次滚动后等待秒数 | `2` |
| `--max-scrolls` | 最大滚动次数（防止死循环） | `50` |
| `--cookies` | 包含浏览器 Cookies 的 JSON 文件路径 | 无 |
| `--list-only` | 仅打印视频列表，不下载 | 关闭 |

### 仅列出视频链接（不下载）

```bash
python douyin_scraper.py --list-only \
  "https://www.douyin.com/user/MS4wLjABAAAAl61SDq2w6mLhMWpv1-ABXqdBRV9nrcyr140Oxf3aPiXE_L0bt5XR15XGm2SajP72"
```

### 使用 Cookie 访问登录内容

1. 在浏览器中登录抖音；
2. 使用浏览器扩展（如 EditThisCookie）导出 JSON 格式 Cookie；
3. 传入 `--cookies` 参数：

```bash
python douyin_scraper.py --cookies cookies.json \
  "https://www.douyin.com/user/<用户ID>"
```

### 在 Python 代码中调用

```python
from douyin_scraper import DouyinScraper

scraper = DouyinScraper(output_dir="my_videos", headless=True)

# 仅获取视频列表
videos = scraper.fetch_video_list(
    "https://www.douyin.com/user/<用户ID>"
)
for v in videos:
    print(v["aweme_id"], v["desc"], v["url"])

# 下载所有视频
scraper.download_all(videos)

# 或一步完成
scraper.run("https://www.douyin.com/user/<用户ID>")
```

## 运行测试

```bash
pip install pytest
pytest test_douyin_scraper.py -v
```

## 注意事项

- 本工具仅限学习研究用途，请勿用于商业目的或侵权行为。
- 部分内容可能需要登录才能访问。
- 下载速度受网络环境及抖音服务器限速影响。
- 抖音前端架构可能随时变化，导致 API 路径失效，请及时更新。