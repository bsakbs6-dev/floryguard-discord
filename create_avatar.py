import os
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from scipy.ndimage import distance_transform_edt, gaussian_filter

def create_floryguard_avatar():
    orig_path = r'C:\Users\NightMare\.gemini\antigravity\brain\d7d553e5-b2c1-46be-8d4d-16bb30a6bd13\.user_uploaded\media_1787912462620.jpg'
    img_bgr = cv2.imread(orig_path)
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    h, w, c = img_rgb.shape

    # 1. Precise mask of 'S' and 'FloryShop' for clean background inpainting
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    mask = np.zeros((h, w), dtype=np.uint8)

    # S ROI
    s_roi = gray[370:645, 485:735]
    thresh_s = cv2.threshold(s_roi, 95, 255, cv2.THRESH_BINARY)[1]
    kernel_s = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (19, 19))
    thresh_s_dil = cv2.dilate(thresh_s, kernel_s, iterations=1)
    mask[370:645, 485:735] = thresh_s_dil

    # FloryShop ROI
    shop_roi = gray[620:725, 290:735]
    thresh_shop = cv2.threshold(shop_roi, 95, 255, cv2.THRESH_BINARY)[1]
    kernel_shop = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    thresh_shop_dil = cv2.dilate(thresh_shop, kernel_shop, iterations=1)
    mask[620:725, 290:735] = thresh_shop_dil

    # Inpaint background
    inpainted_bgr = cv2.inpaint(img_bgr, mask, 9, cv2.INPAINT_TELEA)
    inpainted_rgb = cv2.cvtColor(inpainted_bgr, cv2.COLOR_BGR2RGB)

    # Blend slightly with radial ambient background to ensure perfect smoothness in inner circle
    Y, X = np.ogrid[:h, :w]
    dist_center = np.sqrt((X - 512)**2 + (Y - 512)**2)
    inner_circle = dist_center < 340
    
    # 2. Render 3D Gold Metallic Letter 'G'
    # Target bounds for G: top y~405, bottom y~628, left x~505, right x~705
    # Font: Arial Black or Segoe UI Black
    font_g = ImageFont.truetype(r'C:\Windows\Fonts\ariblk.ttf', 315)
    
    # Render G onto high-res alpha canvas
    canvas_g = Image.new('L', (w, h), 0)
    draw_g = ImageDraw.Draw(canvas_g)
    # Position G to match cap height (y=406) and baseline (y=628) of F
    draw_g.text((505, 362), 'G', font=font_g, fill=255)
    mask_g = np.array(canvas_g, dtype=np.float32) / 255.0

    # 3. Render 3D Gold Metallic Text 'FloryGuard'
    # Font: Georgia Bold
    font_fg_text = ImageFont.truetype(r'C:\Windows\Fonts\georgiab.ttf', 66)
    canvas_text = Image.new('L', (w, h), 0)
    draw_text = ImageDraw.Draw(canvas_text)
    
    text_str = 'FloryGuard'
    # Measure text bounding box to center horizontally
    bbox_t = font_fg_text.getbbox(text_str)
    t_width = bbox_t[2] - bbox_t[0]
    tx = int(512 - t_width / 2)
    ty = 636
    draw_text.text((tx, ty), text_str, font=font_fg_text, fill=255)
    mask_text = np.array(canvas_text, dtype=np.float32) / 255.0

    # Function to apply 3D Gold Metallic Shader to any binary mask
    def render_gold_element(glyph_mask, is_large_letter=True):
        binary = (glyph_mask > 0.3).astype(np.uint8)
        dist_inside = distance_transform_edt(binary)
        dist_outside = distance_transform_edt(1 - binary)

        # 3D Normal map from distance transform
        sobel_x = cv2.Sobel(dist_inside, cv2.CV_32F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(dist_inside, cv2.CV_32F, 0, 1, ksize=3)
        
        # Light direction from top-left (dx=-0.4, dy=-0.7, dz=0.6)
        lx, ly, lz = -0.4, -0.7, 0.6
        norm = np.sqrt(lx**2 + ly**2 + lz**2)
        lx, ly, lz = lx/norm, ly/norm, lz/norm

        # Bevel normal vectors
        nz = 1.8 if is_large_letter else 1.2
        mag = np.sqrt(sobel_x**2 + sobel_y**2 + nz**2)
        nx = -sobel_x / np.maximum(mag, 1e-5)
        ny = -sobel_y / np.maximum(mag, 1e-5)
        nz_unit = nz / np.maximum(mag, 1e-5)

        diffuse = np.maximum(0, nx*lx + ny*ly + nz_unit*lz)
        
        # Specular highlight
        vx, vy, vz = 0, 0, 1 # View vector
        # Half vector
        hx, hy, hz = lx + vx, ly + vy, lz + vz
        hnorm = np.sqrt(hx**2 + hy**2 + hz**2)
        hx, hy, hz = hx/hnorm, hy/hnorm, hz/hnorm
        specular = np.maximum(0, nx*hx + ny*hy + nz_unit*hz) ** 14

        # Vertical base gradient
        y_indices, x_indices = np.where(glyph_mask > 0.05)
        ymin, ymax = y_indices.min(), y_indices.max()
        h_elem = max(ymax - ymin, 1)

        # 6-stop rich gold gradient
        stops = [
            (0.00, np.array([255, 255, 225], dtype=np.float32)), # White gold highlight at top
            (0.12, np.array([255, 244, 120], dtype=np.float32)), # Pale gold
            (0.35, np.array([255, 206,  24], dtype=np.float32)), # Rich radiant gold
            (0.65, np.array([245, 150,   6], dtype=np.float32)), # Warm deep gold
            (0.88, np.array([215,  92,   0], dtype=np.float32)), # Amber/copper
            (1.00, np.array([170,  55,   0], dtype=np.float32))  # Deep bronze at bottom
        ]

        grad_rgb = np.zeros((h, w, 3), dtype=np.float32)
        for y in range(ymin, ymax + 1):
            t = (y - ymin) / float(h_elem)
            for i in range(len(stops)-1):
                if stops[i][0] <= t <= stops[i+1][0]:
                    f = (t - stops[i][0]) / (stops[i+1][0] - stops[i][0])
                    col = (1.0 - f) * stops[i][1] + f * stops[i+1][1]
                    grad_rgb[y, :] = col
                    break

        # Apply 3D bevel & specular shading
        gold_shaded = grad_rgb * (0.65 + 0.45 * diffuse[:, :, None]) + specular[:, :, None] * 180.0
        gold_shaded = np.clip(gold_shaded, 0, 255)

        # Inner rim highlight: 1-2px from edge
        rim_mask = np.exp(-((dist_inside - 1.5)**2) / 1.2) * (diffuse > 0.3)
        gold_shaded = gold_shaded + rim_mask[:, :, None] * np.array([255, 255, 220]) * 0.5
        gold_shaded = np.clip(gold_shaded, 0, 255)

        # Dark border outline
        border_width = 3.5 if is_large_letter else 2.2
        border_mask = np.clip((dist_outside < border_width).astype(np.float32) - (dist_inside > 0).astype(np.float32), 0, 1)
        border_mask = gaussian_filter(border_mask, sigma=0.6)
        border_color = np.array([45, 12, 0], dtype=np.float32) # Dark reddish brown

        # Outer Golden Glow
        glow1 = gaussian_filter(binary.astype(np.float32), sigma=3.0 if is_large_letter else 1.8) * 0.9
        glow2 = gaussian_filter(binary.astype(np.float32), sigma=10.0 if is_large_letter else 5.0) * 0.6
        glow3 = gaussian_filter(binary.astype(np.float32), sigma=25.0 if is_large_letter else 12.0) * 0.35
        glow_total = np.clip(glow1 + glow2 + glow3, 0, 1)
        glow_color = np.array([255, 175, 15], dtype=np.float32)

        # Drop shadow
        shadow_mask = np.zeros_like(binary, dtype=np.float32)
        sy = 7 if is_large_letter else 4
        sx = 2 if is_large_letter else 1
        shadow_mask[sy:, sx:] = binary[:-sy, :-sx]
        shadow_mask = gaussian_filter(shadow_mask, sigma=4.0 if is_large_letter else 2.5) * 0.75
        shadow_color = np.array([18, 4, 0], dtype=np.float32)

        return {
            'mask': glyph_mask,
            'gold': gold_shaded,
            'border_mask': border_mask,
            'border_color': border_color,
            'glow_mask': glow_total,
            'glow_color': glow_color,
            'shadow_mask': shadow_mask,
            'shadow_color': shadow_color
        }

    # Generate shader data
    g_data = render_gold_element(mask_g, is_large_letter=True)
    text_data = render_gold_element(mask_text, is_large_letter=False)

    # Composite layers onto inpainted background
    output = inpainted_rgb.astype(np.float32)

    # Layer 1: Drop shadows
    for elem in [g_data, text_data]:
        sh_m = elem['shadow_mask'][:, :, None]
        sh_c = elem['shadow_color']
        output = output * (1.0 - sh_m * 0.8) + sh_c * (sh_m * 0.8)

    # Layer 2: Golden Outer Glow (Additive / Screen blend)
    for elem in [g_data, text_data]:
        gl_m = elem['glow_mask'][:, :, None]
        gl_c = elem['glow_color']
        # Screen blend
        output = 255.0 - (255.0 - output) * (255.0 - gl_c * gl_m) / 255.0

    # Layer 3: Dark outlines
    for elem in [g_data, text_data]:
        bm = elem['border_mask'][:, :, None]
        bc = elem['border_color']
        output = output * (1.0 - bm) + bc * bm

    # Layer 4: 3D Metallic Gold Glyphs
    for elem in [g_data, text_data]:
        gm = gaussian_filter(elem['mask'], sigma=0.4)[:, :, None]
        gc = elem['gold']
        output = output * (1.0 - gm) + gc * gm

    final_img_arr = np.clip(output, 0, 255).astype(np.uint8)
    final_img = Image.fromarray(final_img_arr)

    # Save to project workspace
    workspace_png = r'c:\Users\NightMare\Desktop\FloryMine 1.21.11\FloryGuardBot\FloryGuard_Avatar.png'
    workspace_jpg = r'c:\Users\NightMare\Desktop\FloryMine 1.21.11\FloryGuardBot\FloryGuard_Avatar.jpg'
    final_img.save(workspace_png, format='PNG', quality=100)
    final_img.save(workspace_jpg, format='JPEG', quality=98)

    # Save to artifact directory
    artifact_png = r'C:\Users\NightMare\.gemini\antigravity\brain\d7d553e5-b2c1-46be-8d4d-16bb30a6bd13\FloryGuard_Avatar.png'
    final_img.save(artifact_png, format='PNG', quality=100)

    print('Successfully generated FloryGuard avatar!')

if __name__ == '__main__':
    create_floryguard_avatar()
