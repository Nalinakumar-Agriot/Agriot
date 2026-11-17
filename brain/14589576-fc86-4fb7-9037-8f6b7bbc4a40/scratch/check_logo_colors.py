import os
from PIL import Image

filepath = r"c:\Users\muthaiah\Desktop\Laravel Projects\AgriOT\src\assets\img\auth\agri_logo_5.png"
if os.path.exists(filepath):
    try:
        with Image.open(filepath) as img:
            img_rgba = img.convert("RGBA")
            green_pixels = 0
            orange_pixels = 0
            white_pixels = 0
            other_pixels = 0
            for y in range(img.size[1]):
                for x in range(img.size[0]):
                    r, g, b, a = img_rgba.getpixel((x, y))
                    if a > 50: # non-transparent
                        # check for green (green component dominant)
                        if g > r + 30 and g > b + 30:
                            green_pixels += 1
                        # check for orange (red dominant, green moderate, low blue)
                        elif r > 200 and g > 100 and b < 80:
                            orange_pixels += 1
                        # check for white/grey
                        elif abs(r - g) < 15 and abs(g - b) < 15 and abs(r - b) < 15:
                            white_pixels += 1
                        else:
                            other_pixels += 1
            print(f"Green: {green_pixels} | Orange: {orange_pixels} | White/Grey: {white_pixels} | Other: {other_pixels}")
    except Exception as e:
        print(f"Error: {e}")
