import os
from PIL import Image

logo_dir = r"c:\Users\muthaiah\Desktop\Laravel Projects\AgriOT\src\assets\img\auth"
for i in [3, 4, 5]:
    filename = f"agri_logo_{i}.png"
    filepath = os.path.join(logo_dir, filename)
    if os.path.exists(filepath):
        try:
            with Image.open(filepath) as img:
                img_rgba = img.convert("RGBA")
                # find non-transparent pixels
                colors = []
                for y in range(img.size[1]):
                    for x in range(img.size[0]):
                        r, g, b, a = img_rgba.getpixel((x, y))
                        if a > 50: # non-transparent
                            colors.append((r, g, b))
                # print average color or count of unique colors
                if colors:
                    avg_color = tuple(sum(c[i] for c in colors) // len(colors) for i in range(3))
                    print(f"File: {filename} | Non-transparent pixels: {len(colors)} | Avg Color (R,G,B): {avg_color}")
                    # sample some non-transparent pixels
                    sample = colors[::len(colors)//5] if len(colors) > 5 else colors
                    print(f"  Samples: {sample}")
        except Exception as e:
            print(f"  Error reading {filename}: {e}")
