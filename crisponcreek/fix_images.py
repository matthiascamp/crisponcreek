"""
Fix image matching issues in top_products.json:
1. Remove "no photo" placeholder images (4418 bytes) - delete files & clear references
2. Fix turmeric image being wrongly assigned to products just because name contains "fresh"
3. Improve image matching using better fuzzy logic against CSV files
4. Search Open Food Facts for products missing images (by barcode)

Usage:
    python fix_images.py

Run from the crisponcreek/ folder.
"""

import json
import os
import re
import csv
import time
import urllib.request
import urllib.error
from difflib import SequenceMatcher

# Config
PRODUCTS_FILE = 'top_products.json'
NO_PHOTO_SIZE = 4418  # exact size of the "no photo" placeholder
DELAY = 0.5

CATEGORY_FOLDERS = {
    'Fruit & Veg': 'crisp_on_creek_fruit_veg_images',
    'Deli': 'crisp_on_creek_deli_images',
    'Grocery': 'crisp_on_creek_images',
}

CSV_FILES = {
    'Fruit & Veg': 'crisp_on_creek_fruit_veg.csv',
    'Deli': 'crisp_on_creek_deli.csv',
    'Grocery': 'crisp_on_creek_groceries.csv',
}


def load_csv_mappings():
    """Load all CSV product-name-to-image mappings."""
    mappings = {}  # category -> {normalized_name: image_filename}
    for category, csv_file in CSV_FILES.items():
        if not os.path.exists(csv_file):
            continue
        mappings[category] = {}
        with open(csv_file, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get('Product Name', '').strip()
                img = row.get('Image Filename', '').strip()
                if name and img:
                    mappings[category][name.upper()] = img
    return mappings


def normalize_name(name):
    """Normalize a product name for matching."""
    s = name.upper().strip()
    # Remove weight/size suffixes for matching
    s = re.sub(r'\b\d+\s*(G|GR|GRM|GM|KG|ML|L|LT|LTR|PK|EA)\b', '', s)
    # Remove special chars
    s = re.sub(r'[^\w\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def find_best_csv_match(product_name, csv_names, threshold=0.6):
    """Find the best matching CSV entry for a product name.
    Uses full-name similarity rather than substring matching to avoid
    the 'fresh' -> 'Fresh Tumeric' problem.
    """
    norm_product = normalize_name(product_name)
    best_score = 0
    best_match = None

    for csv_name in csv_names:
        norm_csv = normalize_name(csv_name)

        # Exact match (after normalization)
        if norm_product == norm_csv:
            return csv_name, 1.0

        # Sequence similarity on full names
        score = SequenceMatcher(None, norm_product, norm_csv).ratio()

        # Bonus: if all words of the shorter name appear in the longer name
        words_p = set(norm_product.split())
        words_c = set(norm_csv.split())
        if words_c and words_p:
            # What fraction of CSV words appear in product name?
            overlap = len(words_c & words_p) / max(len(words_c), len(words_p))
            score = max(score, overlap)

        if score > best_score:
            best_score = score
            best_match = csv_name

    if best_score >= threshold:
        return best_match, best_score
    return None, 0


def is_no_photo(filepath):
    """Check if a file is the 'no photo' placeholder."""
    try:
        return os.path.getsize(filepath) == NO_PHOTO_SIZE
    except OSError:
        return False


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
            if len(data) < 1000:
                return False
            with open(filepath, 'wb') as f:
                f.write(data)
            return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return False


def main():
    # Load products
    with open(PRODUCTS_FILE) as f:
        products = json.load(f)

    # Load CSV mappings
    csv_mappings = load_csv_mappings()
    print(f"Loaded CSV mappings: {', '.join(f'{cat}: {len(m)} entries' for cat, m in csv_mappings.items())}")

    # ============================================================
    # STEP 1: Find and remove "no photo" placeholder images
    # ============================================================
    print("\n=== Step 1: Removing 'no photo' placeholder images ===")
    no_photo_files = set()
    for folder in CATEGORY_FOLDERS.values():
        if not os.path.isdir(folder):
            continue
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            if is_no_photo(fpath):
                no_photo_files.add(os.path.join(folder, fname).replace('\\', '/'))
                # Delete the placeholder file
                os.remove(fpath)

    print(f"Found and deleted {len(no_photo_files)} 'no photo' placeholder images")

    # Clear img references pointing to deleted placeholders
    placeholders_cleared = 0
    for p in products:
        if p.get('img') and p['img'].replace('\\', '/') in no_photo_files:
            p['img'] = ''
            placeholders_cleared += 1
    print(f"Cleared {placeholders_cleared} product references to placeholder images")

    # ============================================================
    # STEP 2: Fix turmeric and other bad fuzzy matches
    # ============================================================
    print("\n=== Step 2: Fixing bad image matches (turmeric bug etc.) ===")

    # Build set of image files that actually belong to a specific product in the CSV
    # so we can detect when a product is using someone else's image
    csv_img_to_name = {}  # image_filename -> csv_product_name
    for cat, mapping in csv_mappings.items():
        folder = CATEGORY_FOLDERS.get(cat, '')
        for csv_name, img_file in mapping.items():
            img_path = folder + '/' + img_file
            csv_img_to_name[img_path] = csv_name

    fixed_bad = 0
    for p in products:
        img = p.get('img', '')
        if not img:
            continue

        # Check if this image belongs to a different product in the CSV
        if img in csv_img_to_name:
            owner_name = csv_img_to_name[img]
            # Does this product's name match the image owner?
            match, score = find_best_csv_match(p['name'], [owner_name], threshold=0.5)
            if match is None:
                # This product is using an image that belongs to a different product
                print(f"  BAD MATCH: '{p['name']}' was using image for '{owner_name}' -> cleared")
                p['img'] = ''
                fixed_bad += 1

    print(f"Fixed {fixed_bad} bad image assignments")

    # ============================================================
    # STEP 3: Re-match products without images using improved logic
    # ============================================================
    print("\n=== Step 3: Re-matching products without images ===")

    # Build set of available image files
    available_images = {}  # folder -> set of filenames
    for folder in CATEGORY_FOLDERS.values():
        if os.path.isdir(folder):
            available_images[folder] = set(os.listdir(folder))

    rematched = 0
    for p in products:
        if p.get('img'):
            continue  # already has a valid image

        cat = p.get('category', 'Grocery')
        cat_csv = csv_mappings.get(cat, {})
        if not cat_csv:
            continue

        match, score = find_best_csv_match(p['name'], cat_csv.keys(), threshold=0.65)
        if match:
            img_file = cat_csv[match]
            folder = CATEGORY_FOLDERS.get(cat, 'crisp_on_creek_images')
            img_path = folder + '/' + img_file
            # Make sure the file actually exists and isn't a placeholder
            if img_file in available_images.get(folder, set()):
                p['img'] = img_path
                rematched += 1
                if rematched <= 20:
                    print(f"  MATCHED: '{p['name']}' -> '{match}' (score={score:.2f})")

    if rematched > 20:
        print(f"  ... and {rematched - 20} more")
    print(f"Re-matched {rematched} products to existing images")

    # ============================================================
    # STEP 4: Fetch images from Open Food Facts for remaining
    # ============================================================
    print("\n=== Step 4: Fetching missing images from Open Food Facts ===")
    missing = [p for p in products if not p.get('img') and p.get('barcode')]
    print(f"{len(missing)} products have barcodes but no image - searching Open Food Facts...")

    found = 0
    failed = 0
    for i, p in enumerate(missing):
        barcode = p['barcode']
        img_url = fetch_openfoodfacts(barcode)
        if img_url:
            folder = CATEGORY_FOLDERS.get(p['category'], 'crisp_on_creek_images')
            os.makedirs(folder, exist_ok=True)
            filename = safe_filename(p['name'])
            filepath = os.path.join(folder, filename)

            if download_image(img_url, filepath):
                # Double check it's not another "no photo" placeholder
                if is_no_photo(filepath):
                    os.remove(filepath)
                    failed += 1
                else:
                    p['img'] = folder + '/' + filename
                    found += 1
                    print(f"  [OK] {p['name']} -> {filename}")
            else:
                failed += 1
        else:
            failed += 1

        time.sleep(DELAY)
        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(missing)}] {found} found, {failed} not found...")
            with open(PRODUCTS_FILE, 'w') as f:
                json.dump(products, f, indent=2)

    print(f"Downloaded {found} new images from Open Food Facts")

    # ============================================================
    # SAVE
    # ============================================================
    with open(PRODUCTS_FILE, 'w') as f:
        json.dump(products, f, indent=2)

    # Summary
    total = len(products)
    with_img = sum(1 for p in products if p.get('img'))
    without_img = total - with_img
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total products:           {total}")
    print(f"With images:              {with_img} ({with_img*100//total}%)")
    print(f"Without images:           {without_img} ({without_img*100//total}%)")
    print(f"Placeholders removed:     {len(no_photo_files)}")
    print(f"Bad matches fixed:        {fixed_bad}")
    print(f"Re-matched from CSV:      {rematched}")
    print(f"Downloaded from OFF:      {found}")


if __name__ == '__main__':
    main()
