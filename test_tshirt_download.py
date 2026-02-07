"""
Download clothing item images using GPT-4o Search Preview's web search.
"""

import os
import json
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.getenv('API_KEY'))
images_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
os.makedirs(images_dir, exist_ok=True)

def download_image(url, filepath):
    """Download image from URL."""
    try:
        print(f"  [DEBUG] Downloading from: {url}")
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15, stream=True)
        response.raise_for_status()
        
        content_type = response.headers.get('content-type', '').lower()
        print(f"  [DEBUG] Content-Type: {content_type}")
        
        if 'image' not in content_type:
            print(f"  [DEBUG] Not an image, skipping")
            return False
        
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        file_size = os.path.getsize(filepath)
        print(f"  [DEBUG] Downloaded {file_size} bytes")
        return file_size > 1000
    except Exception as e:
        print(f"  [DEBUG] Download error: {e}")
        return False

def search_and_download(item="t-shirt", brand=None):
    """Search web with GPT and download image."""
    query = f"{brand} {item}" if brand else item
    filename = f"{brand or 'item'}_{item.replace(' ', '_')}.jpg"
    filepath = os.path.join(images_dir, filename)
    
    print(f"[DEBUG] Query: {query}")
    print(f"[DEBUG] Output file: {filepath}")
    
    print(f"Searching for: {query}...")
    
    # Build prompt based on whether brand is specified
    if brand:
        prompt = f"""Search the web and find REAL, WORKING product image URLs of {brand} {item} from the official {brand} website.

CRITICAL REQUIREMENTS:
- Search actual brand websites (nike.com, adidas.com, etc.) and find REAL product image URLs
- URLs must be from official brand domains (e.g., static.nike.com, images.adidas.com)
- Only return URLs that actually exist and are accessible
- Each URL must be a direct link to an image file (ending in .jpg, .jpeg, .png, .gif, or .webp)
- DO NOT generate example URLs or placeholder URLs
- DO NOT use stock photo sites, Wikipedia, or generic image hosting

Return ONLY a JSON array of REAL, WORKING direct image URLs from brand websites:
["https://real-url-from-brand.com/image1.jpg", "https://real-url-from-brand.com/image2.jpg"]

Return at least 3-5 URLs from actual brand product pages."""
    else:
        prompt = f"""Search the web and find REAL, WORKING product image URLs of {item} from official brand websites.

CRITICAL REQUIREMENTS:
- Search actual brand websites and find REAL product image URLs
- URLs must be from official brand domains
- Only return URLs that actually exist and are accessible
- Each URL must be a direct link to an image file (ending in .jpg, .jpeg, .png, .gif, or .webp)
- DO NOT generate example URLs or placeholder URLs
- DO NOT use stock photo sites, Wikipedia, or generic image hosting

Return ONLY a JSON array of REAL, WORKING direct image URLs from brand websites:
["https://real-url-from-brand.com/image1.jpg", "https://real-url-from-brand.com/image2.jpg"]

Return at least 3-5 URLs from actual brand product pages."""
    
    response = client.chat.completions.create(
        model="gpt-4o-search-preview",
        messages=[{
            "role": "user",
            "content": prompt
        }]
    )
    
    raw_response = response.choices[0].message.content
    print(f"[DEBUG] Raw GPT response: {raw_response[:200]}...")
    
    # Extract JSON from markdown code blocks if present
    if '```json' in raw_response:
        start = raw_response.find('```json') + 7
        end = raw_response.find('```', start)
        raw_response = raw_response[start:end].strip()
        print(f"[DEBUG] Extracted JSON from code block")
    elif '```' in raw_response:
        start = raw_response.find('```') + 3
        end = raw_response.find('```', start)
        raw_response = raw_response[start:end].strip()
        print(f"[DEBUG] Extracted from code block")
    
    # Parse JSON response
    urls = []
    try:
        urls = json.loads(raw_response)
        print(f"[DEBUG] Parsed {len(urls)} URLs from JSON")
    except json.JSONDecodeError as e:
        print(f"[DEBUG] JSON parse failed: {e}")
        print(f"[DEBUG] Trying to extract URLs from text...")
        # If not JSON, try to extract from text
        urls = [line.strip() for line in raw_response.split('\n') if line.strip().startswith('http')]
        print(f"[DEBUG] Extracted {len(urls)} URLs from text")
    
    if not urls:
        print("[DEBUG] No URLs found in response")
        print(f"[DEBUG] Full response: {raw_response}")
        return None
    
    print(f"[DEBUG] Found {len(urls)} URL(s): {urls}")
    
    # Try downloading each URL
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] Trying: {url[:60]}...")
        if download_image(url, filepath):
            print(f"✓ Image downloaded and saved")
            return filepath
    
    print("✗ Failed to download image")
    return None

if __name__ == "__main__":
    import sys
    item = sys.argv[1] if len(sys.argv) > 1 else "t-shirt"
    brand = sys.argv[2] if len(sys.argv) > 2 else None
    
    result = search_and_download(item, brand)
    if result:
        print(f"\n✓ File saved: {result}")
        print(f"  File path: {os.path.abspath(result)}")
    else:
        print(f"\n✗ Failed to download image")
