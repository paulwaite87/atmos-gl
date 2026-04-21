#!/home/pj/venv/bin/python3

import os
import argparse
from PIL import Image

def precise_crop(img, v_offset=0):
    """
    Crops the image to a 2:1 ratio with a manual vertical offset adjustment.
    """
    width, height = img.size
    target_height = width // 2
    
    # Calculate base centered padding
    total_padding = height - target_height
    # Apply the offset to the top padding calculation
    # Since you said it's too far North, you likely need to 
    # DECREASE top_padding (negative offset) to move the outlines South.
    top_padding = (total_padding // 2) + v_offset
    
    # Safety check to keep crop within image bounds
    if top_padding < 0: top_padding = 0
    if top_padding + target_height > height: top_padding = height - target_height

    print(f"Applying Vertical Offset: {v_offset}")
    print(f"Crop coordinates: Top={top_padding}, Bottom={top_padding + target_height}")
    
    return img.crop((0, top_padding, width, top_padding + target_height))

def process_images(working_dir, outline_file, day_file, night_file, v_offset):
    working_dir = os.path.abspath(working_dir)
    
    path_outline = os.path.join(working_dir, outline_file)
    path_day = os.path.join(working_dir, day_file)
    path_night = os.path.join(working_dir, night_file)

    try:
        outline = Image.open(path_outline).convert("RGBA")
        
        # 1. Precise Crop with manual offset
        outline_cropped = precise_crop(outline, v_offset)
        
        # 2. Color Fix (White lines)
        r, g, b, alpha = outline_cropped.split()
        new_color_layer = Image.new("RGB", outline_cropped.size, (255, 255, 255))
        fixed_outline = Image.merge("RGBA", (*new_color_layer.split(), alpha))

        day = Image.open(path_day).convert("RGBA")
        night = Image.open(path_night).convert("RGBA")

        # 3. Resize to match base map
        if day.size != fixed_outline.size:
            fixed_outline = fixed_outline.resize(day.size, Image.Resampling.LANCZOS)

        # 4. Composite and Save
        for base_img, name in [(day, day_file), (night, night_file)]:
            combined = Image.alpha_composite(base_img, fixed_outline)
            base_name, ext = os.path.splitext(name)
            out_path = os.path.join(working_dir, f"{base_name}_outline{ext}")
            combined.convert("RGB").save(out_path, quality=95)
            print(f"Saved: {out_path}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workingdir", required=True)
    parser.add_argument("--outline", required=True)
    parser.add_argument("--day", required=True)
    parser.add_argument("--night", required=True)
    parser.add_argument("--voffset", type=int, default=0, help="Adjustment for vertical alignment")

    args = parser.parse_args()
    process_images(args.workingdir, args.outline, args.day, args.night, args.voffset)

