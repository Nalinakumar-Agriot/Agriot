import os
from PIL import Image

logo_dir = r"c:\Users\muthaiah\Desktop\Laravel Projects\AgriOT\src\assets\img\auth"
for i in [3, 4, 5]:
    filename = f"agri_logo_{i}.png"
    filepath = os.path.join(logo_dir, filename)
    if os.path.exists(filepath):
        try:
            with Image.open(filepath) as img:
                # convert to RGBA if not already
                img_rgba = img.convert("RGBA")
                # get corner pixel values
                pixels = [
                    img_rgba.getpixel((0, 0)),
                    img_rgba.getpixel((img.size[0] - 1, 0)),
                    img_rgba.getpixel((0, img.size[1] - 1)),
                    img_rgba.getpixel((img.size[0] - 1, img.size[1] - 1)),
                ]
                print(f"File: {filename}")
                print(f"  Corner pixels (R, G, B, A): {pixels}")
                # check if there are transparent pixels
                alpha_channel = img_rgba.split()[3]
                transparent_pixels = sum(1 for p in alpha_channel.getdata() if p == 0)
                total_pixels = img.size[0] * img.size[1]
                print(f"  Transparent pixels: {transparent_pixels} / {total_pixels} ({transparent_pixels/total_pixels:.1%})")
        except Exception as e:
            print(f"  Error reading {filename}: {e}")
