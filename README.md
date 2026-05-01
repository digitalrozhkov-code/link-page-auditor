# 🔍 Link Page Auditor

Automatic page review before adding links, based on an SEO audit checklist.

---

## 📋 What the script checks

| # | Verification | Description |
|---|----------|----------|
| 1 | **robots.txt** | Is the path blocked by a directive? `Disallow` |
| 2 | **meta robots noindex** | Is there a `noindex` attribute in the `<meta name="robots">` tag in the `<head>` |
| 3 | **X-Robots-Tag** | Is there a `noindex` in the server's HTTP response header? |
| 4 | **Site operator:** | Is this page indexed by Google? |
| 5 | **Canonical** | Does `<link rel="canonical">` match the page's URL? Determines whether the canonical URL is set via JavaScript (not counted) |
| 6 | **HTML Links** | Are there any `<a href>` tags in the source code (not the DOM)? |
| 7 | **JS links** | Are there any `onclick=window.open(...)` or `href=javascript:` |
| 8 | **Redirect gateway** | Is there a redirect via an external gateway (`go.php`, `redirect`, `out.php`, etc.) |

---

## ⚙️ Installation

### Requirements
- Python 3.9+
- Windows / macOS / Linux

### Steps

```bash
# 1. Clone a repository
git clone https://github.com/ВАШ_ЮЗЕРНЕЙМ/link-page-auditor.git
cd link-page-auditor

# 2. Set dependencies
pip install playwright requests beautifulsoup4 lxml playwright-stealth

# 3. Install the Chromium browser
python -m playwright install chromium
```

---

## 🚀 Launch

### A single URL
```bash
python auditor.py https://forum.example.com/topic/123
```

### Multiple URLs at once
```bash
python auditor.py https://site1.com/page https://site2.com/forum/thread
```

### List of URLs from a file
Create a file named `urls.txt`—one URL per line; lines starting with `#` are ignored:
