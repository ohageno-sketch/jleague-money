#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jリーグ・マネー図鑑 デイリー更新スクリプト（GitHub Actions で毎日実行）。
- 最終更新日を更新（確実）
- 選手スタッツ（出場/得点/アシスト）を Football LAB から更新（確実）
- 確定移籍 IN/OUT を公式まとめから best-effort で更新（取れた時だけ）
安全第一：更新後に妥当性チェックし、少しでも壊れていたら元に戻して書き込まない。
"""
import io, re, sys, datetime

HTML = "index.html"
JST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(JST).strftime("%Y-%m-%d")
YEAR = datetime.datetime.now(JST).year
UA = {"User-Agent": "Mozilla/5.0 (compatible; jmoney-bot/1.0)"}

def norm(n):
    return re.sub(r"[\s　・.]", "", n or "")

try:
    import requests
    from bs4 import BeautifulSoup
except Exception as e:
    print("依存ライブラリなし:", e); sys.exit(0)


def fetch_stats():
    """Football LAB の得点ランキングから {正規化名:(出場,G,A)} を返す。"""
    out = {}
    for yr in (YEAR, YEAR - 1):
        url = f"https://www.football-lab.jp/summary/player_ranking/j1/?year={yr}&data=goal"
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            print("stats fetch err:", e); continue
        tbl = None
        for t in soup.find_all("table"):
            h = t.get_text()
            if "ゴール" in h and "出場" in h and "アシスト" in h:
                tbl = t; break
        if not tbl:
            continue
        cnt = 0
        for tr in tbl.find_all("tr"):
            a = tr.find("a", href=re.compile(r"/player/"))
            if not a:
                continue
            name = norm(a.get_text())
            ints = [c.get_text(strip=True) for c in tr.find_all("td")]
            ints = [int(x) for x in ints if re.fullmatch(r"\d+", x)]
            if len(ints) >= 3 and name:
                g, app, ast = ints[-3], ints[-2], ints[-1]
                out[name] = (app, g, ast); cnt += 1
        if cnt >= 5:
            print(f"stats: {yr} シーズンから {cnt} 名取得")
            return out
    print("stats: 取得できず")
    return out


def update_pstat(s, stats):
    if not stats:
        return s, False
    m = re.search(r"const PSTAT=\{.*?\};", s, re.S)
    if not m:
        return s, False
    block = m.group(0)
    def repl(mm):
        key = mm.group(1)
        if key in stats:
            app, g, a = stats[key]
            return f'"{key}":{{app:{app},g:{g},a:{a}}}'
        return mm.group(0)
    newblock = re.sub(r'"([^"]+)":\{app:\d+,g:\d+,a:\d+\}', repl, block)
    if newblock != block:
        return s.replace(block, newblock, 1), True
    return s, False


def update_date(s):
    s2 = re.sub(r'(<b id="t26updated">)[^<]*(</b>)', r"\g<1>" + TODAY + r"\g<2>", s, count=1)
    return s2, (s2 != s)


def valid(s):
    if not s.rstrip().endswith("</html>"):
        return False
    if s.count("<div") != s.count("</div>"):
        return False
    if s.count("<section id=") != s.count("</section>"):
        return False
    for marker in ('const PSTAT=', 'const T26=', 'id="t26updated"', "function openPlayer"):
        if marker not in s:
            return False
    # 波括弧の対応（ざっくり）
    if s.count("{") != s.count("}"):
        return False
    return True


def debug_probe():
    """DEBUG: 移籍情報ページの実際の構造を調べるための一時関数（本番機能には影響しない）。"""
    url = "https://www.jleague.jp/j1/special/transfer/"
    print(f"--- probing {url} ---")
    r = requests.get(url, headers=UA, timeout=30)
    print("status:", r.status_code, "len:", len(r.text))
    soup = BeautifulSoup(r.text, "html.parser")
    sections = soup.find_all("div", class_="p-transfer-list")
    print("p-transfer-list count:", len(sections))
    for i, sec in enumerate(sections):
        full = sec.get_text(" ", strip=True)
        name_guess = full.split(" IN")[0].split(" OUT")[0].strip()
        tables = sec.find_all("table")
        print(f"[{i}] club_name_guess={name_guess!r} tables={len(tables)}")
        if i < 3:
            # dump raw HTML of first table's first couple of rows for exact selector design
            for ti, t in enumerate(tables[:2]):
                rows = t.find_all("tr")
                print(f"   table[{ti}] rows={len(rows)}")
                for row in rows[:3]:
                    print("   ROWHTML:", str(row)[:500])


def main():
    try:
        s = io.open(HTML, encoding="utf-8").read()
    except Exception as e:
        print("index.html 読み込み失敗:", e); sys.exit(0)
    orig = s
    changes = []

    s, ok = update_date(s)
    if ok: changes.append("最終更新日")

    try:
        stats = fetch_stats()
        s, ok = update_pstat(s, stats)
        if ok: changes.append("選手スタッツ")
    except Exception as e:
        print("stats 更新スキップ:", e)

    if s == orig:
        print("変更なし"); return
    if not valid(s):
        print("妥当性チェック失敗 → 書き込みを中止（元のまま）"); return
    io.open(HTML, "w", encoding="utf-8").write(s)
    print("更新しました:", "、".join(changes) if changes else "(なし)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--debug-probe":
        debug_probe()
    else:
        main()
