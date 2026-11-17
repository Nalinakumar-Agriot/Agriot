import os
from PIL import Image

logo_dir = r"c:\Users\muthaiah\Desktop\Laravel Projects\AgriOT\src\assets\img\auth"
for i in range(1, 6):
    filename = f"agri_logo_{i}.png"
    filepath = os.path.join(logo_dir, filename)
    if os.path.exists(filepath):
        try:
            with Image.open(filepath) as img:
                print(f"File: {filename} | Format: {img.format} | Size: {img.size} | Mode: {img.mode}")
        except Exception as e:
            print(f"Error reading {filename}: {e}")
    else:
        print(f"{filename} not found")
