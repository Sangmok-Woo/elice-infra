"""body*.html + style.css + figs/*.svg -> proposal*.html

  python build.py          body.html      -> proposal.html       (기본판)
  python build.py easy     body-easy.html -> proposal-easy.html  (쉬운 버전)
"""
import sys
from pathlib import Path
d = Path(__file__).parent
suffix = "-" + sys.argv[1] if len(sys.argv) > 1 else ""
body = (d / f"body{suffix}.html").read_text(encoding="utf-8")
for i in range(1, 9):
    body = body.replace("{{FIG%d}}" % i, (d / "figs" / f"fig{i}.svg").read_text(encoding="utf-8"))
css = (d / "style.css").read_text(encoding="utf-8")
html = f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>데이터센터 네트워크 제안서</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
{css}
</style>
</head>
<body>
{body}
</body>
</html>
'''
(d / f"proposal{suffix}.html").write_text(html, encoding="utf-8", newline="\n")
print(f"built proposal{suffix}.html")
