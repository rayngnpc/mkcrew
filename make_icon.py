# make_icon.py -- generate assets/mkcrew.ico: a green ">_" terminal prompt on a dark rounded square.
# One-time:  .venv\Scripts\python -m pip install pillow ; python make_icon.py
from pathlib import Path
from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
BG    = (13, 18, 30, 255)     # studio dark
GREEN = (63, 185, 80, 255)    # MKCREW brand green

d.rounded_rectangle([6, 6, S - 6, S - 6], radius=48, fill=BG)
# ">" chevron (thick polyline, rounded joint)
d.line([(78, 80), (150, 128), (78, 176)], fill=GREEN, width=26, joint="curve")
# rounded end-caps for the chevron (Pillow lines are butt-capped)
for pt in [(78, 80), (150, 128), (78, 176)]:
    d.ellipse([pt[0] - 13, pt[1] - 13, pt[0] + 13, pt[1] + 13], fill=GREEN)
# "_" cursor
d.rounded_rectangle([164, 168, 206, 184], radius=8, fill=GREEN)

out = Path(__file__).parent / "assets" / "mkcrew.ico"
out.parent.mkdir(exist_ok=True)
img.save(out, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(out.with_suffix(".png"))   # preview
print("wrote", out)
