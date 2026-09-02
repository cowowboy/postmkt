"""換址點一致性守門。

index.html 有三個地方寫著站台位址,但只有一個能用變數插值:
  * SITE 物件(JS)            —— 可插值,其餘 JS 全部由它衍生
  * 第 10 行的 CSP 白名單     —— meta 標籤,不能插值
  * 「← 回 Hub」靜態連結      —— 純 HTML,不能插值
加上 src/sites.py 的 Python 側,共四份。

CSP 沒跟著改是最難查的壞法:瀏覽器**靜默**擋掉對新網域的 fetch,頁面只是空白,
不會有任何伺服器端錯誤。這支測試把「忘記同步」變成 CI 就會擋下來的失敗。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

HTML = (ROOT / "index.html").read_text(encoding="utf-8")


def _site() -> dict:
    """從 index.html 抽出 SITE 物件的三個值。"""
    m = re.search(r"const SITE = \{(.*?)\};", HTML, re.S)
    assert m, "index.html 找不到 SITE 物件"
    body = m.group(1)
    out = {}
    for key in ("rawOrg", "worker", "hub"):
        km = re.search(rf'{key}:\s*"([^"]+)"', body)
        assert km, f"SITE 缺少 {key}"
        out[key] = km.group(1)
    return out


SITE = _site()


def test_python_and_frontend_share_the_same_origin():
    from sites import HUB, RAW_ORG, WORKER

    assert RAW_ORG == SITE["rawOrg"], f"src/sites.py RAW_ORG={RAW_ORG} 與前端 SITE.rawOrg={SITE['rawOrg']} 不一致"
    assert WORKER == SITE["worker"], f"src/sites.py WORKER={WORKER} 與前端 SITE.worker={SITE['worker']} 不一致"
    assert HUB == SITE["hub"], f"src/sites.py HUB={HUB} 與前端 SITE.hub={SITE['hub']} 不一致"


def test_csp_whitelists_every_host_the_page_actually_calls():
    """CSP 沒放行 = 瀏覽器靜默擋掉,頁面空白但伺服器端一切正常。"""
    m = re.search(r'Content-Security-Policy"[^>]*content="([^"]+)"', HTML)
    assert m, "找不到 CSP meta"
    connect = re.search(r"connect-src([^;\"]+)", m.group(1))
    assert connect, "CSP 沒有 connect-src"
    allowed = connect.group(1).split()

    for label, url in (("SITE.worker", SITE["worker"]), ("SITE.rawOrg", SITE["rawOrg"])):
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        assert origin in allowed, (
            f"CSP connect-src 沒有放行 {label} 的來源 {origin}\n"
            f"  目前白名單: {allowed}\n"
            f"  改了 SITE 就要一起改第 10 行的 CSP"
        )


def test_the_static_hub_link_matches_site():
    """『← 回 Hub』是純 HTML,插不了值,只能靠這裡守住。"""
    m = re.search(r'<a class="back" href="([^"]+)">', HTML)
    assert m, "找不到「回 Hub」連結"
    assert m.group(1).rstrip("/") == SITE["hub"].rstrip("/"), (
        f"Hub 連結 {m.group(1)} 與 SITE.hub {SITE['hub']} 不一致"
    )


def test_no_hardcoded_addresses_left_in_javascript():
    """防回歸:JS 裡不得再出現寫死的位址,一律走 SITE / RAW()。

    兩個插不了值的地方(CSP meta、回 Hub 靜態連結)不算違規 —— 它們各自有
    專屬測試守著(test_csp_whitelists... / test_the_static_hub_link...)。
    """
    guarded = ('Content-Security-Policy', 'class="back"')
    offenders = []
    for i, line in enumerate(HTML.splitlines(), 1):
        t = line.strip()
        if t.startswith(("//", "<!--")) or "rawOrg:" in t or "worker:" in t or "hub:" in t:
            continue
        if any(g in t for g in guarded):
            continue
        if re.search(r'"https://(raw\.githubusercontent\.com|[a-z0-9-]+\.github\.io|[a-z0-9-]+\.workers\.dev)', t):
            offenders.append(f"  {i}: {t[:100]}")
    assert not offenders, "還有寫死的位址(應改用 SITE/RAW()):\n" + "\n".join(offenders)


def test_github_links_and_api_calls_use_the_same_owner_as_site():
    """2026-09-02 補:第一次換址漏掉整整一類——不是 raw 網址但一樣綁帳號的地方
    (footer 的 github.com 連結、api.github.com 呼叫)。它們不會讓頁面壞掉,
    只會安靜地連到原作者的 repo。"""
    owner = SITE["rawOrg"].rstrip("/").split("/")[-1]
    bad = []
    for i, line in enumerate(HTML.splitlines(), 1):
        t = line.strip()
        if t.startswith(("//", "<!--")):
            continue
        for m in re.finditer(r"https://(?:api\.)?github\.com/(?:repos/)?([A-Za-z0-9_.-]+)", t):
            if "${" in t:          # 已經插值的不算
                continue
            if m.group(1) != owner:
                bad.append(f"  {i}: {t[:90]}")
    assert not bad, f"github 位址的帳號與 SITE.rawOrg({owner})不一致:\n" + "\n".join(bad)
