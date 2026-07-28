#!/usr/bin/env python3
"""TEMPORARY: probe official financial data sources structure (deleted after use)."""
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (compatible; jmoney-bot/1.0)"}

print("=== management page ===")
r = requests.get("https://www.jleague.jp/aboutj/management/", headers=UA, timeout=30)
print("status:", r.status_code, "len:", len(r.text))
soup = BeautifulSoup(r.text, "html.parser")
print("title:", soup.title.get_text() if soup.title else None)
print("table count:", len(soup.find_all("table")))
for t in soup.find_all("table")[:3]:
    print("--- table sample ---")
    print(str(t)[:800])
links = [a.get("href") for a in soup.find_all("a", href=True) if "pdf" in a.get("href", "").lower()]
print("pdf links found:", links[:10])

print("\n=== club_doc-2025.pdf ===")
r2 = requests.get("https://aboutj.jleague.jp/corporate/assets/pdf/club_info/club_doc-2025.pdf", headers=UA, timeout=30)
print("status:", r2.status_code, "content-type:", r2.headers.get("content-type"), "size:", len(r2.content))
with open("club_doc-2025.pdf", "wb") as f:
    f.write(r2.content)

try:
    import pdfplumber
    with pdfplumber.open("club_doc-2025.pdf") as pdf:
        print("num pages:", len(pdf.pages))
        for i, page in enumerate(pdf.pages[:3]):
            print(f"--- page {i} text (first 1200 chars) ---")
            print((page.extract_text() or "")[:1200])
            tables = page.extract_tables()
            print(f"page {i} tables found:", len(tables))
            if tables:
                for row in tables[0][:6]:
                    print("ROW:", row)
except Exception as e:
    print("pdfplumber err:", e)
