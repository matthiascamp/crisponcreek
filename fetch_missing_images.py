"""
Fetch missing product images from Open Food Facts (free, no API key needed).
Uses product barcodes to look up images.

Usage:
    python fetch_missing_images.py

This will:
1. Read missing_images.json for products without photos
2. Look up each barcode on Open Food Facts
3. Download the product image to the appropriate folder
4. Update top_products.json with the new image paths

Run from the crisponcreek/ folder.
"""

import json
import os
import re
import time
import urllib.request
import urllib.error

# Config
MISSING_FILE = 'missing_images.json'
PRODUCTS_FILE = 'top_products.json'
DELAY = 0.5  # seconds between API calls (be polite)

CATEGORY_FOLDERS = {
    'Fruit & Veg': 'crisp_on_creek_fruit_veg_images',
    'Deli': 'crisp_on_creek_deli_images',
    'Grocery': 'crisp_on_creek_images',
}

def safe_filename(name):
    """Convert product name to a safe filename."""
    s = name.strip().title().replace(' ', '_')
    s = re.sub(r'[^\w\-.]', '', s)
    return s[:80] + '.jpg'

def fetch_openfoodfacts(barcode):
    """Look up a product on Open Food Facts by barcode. Returns image URL or None."""
    url = f'https://world.openfoodfacts.org/api/v2/product/{barcode}.json?fields=image_front_url,image_url,product_name'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'CrispOnCreek-ImageFetcher/1.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get('status') == 1:
                product = data.get('product', {})
                return product.get('image_front_url') or product.get('image_url')
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        pass
    return None

def download_image(url, filepath):
    """Download an image from a URL to a local file."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'CrispOnCreek-ImageFetcher/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            if len(data) < 1000:  # too small, probably not a real image
                return False
            with open(filepath, 'wb') as f:
                f.write(data)
            return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return False

def main():
    with open(MISSING_FILE) as f:
        missing = json.load(f)

    with open(PRODUCTS_FILE) as f:
        products = json.load(f)

    # Build lookup: pcode -> product index
    pcode_idx = {}
    for i, p in enumerate(products):
        pcode_idx[p['pcode']] = i

    # Ensure folders exist
    for folder in CATEGORY_FOLDERS.values():
        os.makedirs(folder, exist_ok=True)

    found = 0
    skipped = 0
    failed = 0
    total_with_bc = sum(1 for m in missing if m.get('barcode'))

    print(f"Processing {len(missing)} missing products ({total_with_bc} with barcodes)...")
    print()

    for i, m in enumerate(missing):
        barcode = m.get('barcode', '')
        if not barcode:
            skipped += 1
            continue

        # Look up on Open Food Facts
        img_url = fetch_openfoodfacts(barcode)
        if not img_url:
            failed += 1
            if (i + 1) % 50 == 0:
                print(f"  [{i+1}/{len(missing)}] {found} found, {failed} not found...")
            time.sleep(DELAY)
            continue

        # Download the image
        folder = CATEGORY_FOLDERS.get(m['category'], 'crisp_on_creek_images')
        filename = safe_filename(m['name'])
        filepath = os.path.join(folder, filename)

        if download_image(img_url, filepath):
            # Update the product in top_products.json
            idx = pcode_idx.get(m['pcode'])
            if idx is not None:
                products[idx]['img'] = folder + '/' + filename
            found += 1
            print(f"  [OK] {m['name']} -> {filename}")
        else:
            failed += 1

        time.sleep(DELAY)

        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(missing)}] {found} found, {failed} not found...")
            # Save progress periodically
            with open(PRODUCTS_FILE, 'w') as f:
                json.dump(products, f, indent=2)

    # Final save
    with open(PRODUCTS_FILE, 'w') as f:
        json.dump(products, f, indent=2)

    total_img = sum(1 for p in products if p.get('img'))
    print()
    print(f"Done!")
    print(f"  Found & downloaded: {found}")
    print(f"  Not on Open Food Facts: {failed}")
    print(f"  Skipped (no barcode): {skipped}")
    print(f"  Total products with images: {total_img}/{len(products)} ({total_img*100//len(products)}%)")

if __name__ == '__main__':
    main()
