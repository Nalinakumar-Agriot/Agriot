import os
from PIL import Image
from collections import Counter

filepath = r"c:\Users\muthaiah\Desktop\Laravel Projects\AgriOT\src\assets\img\auth\agri_logo_5.png"
if os.path.exists(filepath):
    try:
        with Image.open(filepath) as img:
            img_rgba = img.convert("RGBA")
            colors = []
            for y in range(img.size[1]):
                for x in range(img.size[0]):
                    r, g, b, a = img_rgba.getpixel((x, y))
                    if a > 0: # not fully transparent
                        colors.append((r, g, b, a))
            counter = Counter(colors)
            print("Top 10 colors in Logo 5:")
            for color, count in counter.most_common(10):
                print(f"  Color: {color} | Count: {count}")
    except Exception as e:
        print(f"Error: {e}")
