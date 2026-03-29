#!/usr/bin/env python3
"""
Download files from sources.yaml to local paths.
"""

import yaml
import requests
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
import sys


def load_sources(yaml_path: str = "sources.yaml") -> Dict:
    """Load and parse the sources.yaml file."""
    with open(yaml_path, 'r') as f:
        return yaml.safe_load(f)


def download_file(url: str, local_path: str) -> bool:
    """Download a file from URL to local path.
    
    Args:
        url: The URL to download from
        local_path: The local file path to save to
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Create directory if it doesn't exist
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Check if file already exists
        if Path(local_path).exists():
            print(f"  ⏭️  Already exists: {local_path}")
            return True
        
        print(f"  📥 Downloading: {url}")
        print(f"     → {local_path}")
        
        # Download the file
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        # Write to file
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        print(f"  ✅ Successfully downloaded to {local_path}")
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"  ❌ Error downloading {url}: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Unexpected error: {e}")
        return False


def iter_source_items(sources: Dict) -> Iterable[Tuple[str, Dict]]:
    """Yield (category, item) pairs from supported source groups.

    Prioritize known categories, but keep support for any top-level list category.
    """
    preferred_order = ["annual_reports", "stamma_protocols"]
    remaining = [k for k in sources.keys() if k not in preferred_order]

    for category in preferred_order + remaining:
        items = sources.get(category)
        if isinstance(items, list):
            for item in items:
                yield category, item


def main():
    """Main function to download all files from sources.yaml."""
    print("🚀 Starting download of source files...\n")
    
    # Load sources
    try:
        sources = load_sources()
    except FileNotFoundError:
        print("❌ Error: sources.yaml file not found")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"❌ Error parsing sources.yaml: {e}")
        sys.exit(1)
    
    # Track statistics
    total_files = 0
    successful = 0
    failed = 0
    skipped = 0
    
    # Process each category
    current_category = None
    for category, item in iter_source_items(sources):
        if category != current_category:
            if current_category is not None:
                print()  # Empty line between categories
            print(f"📁 Category: {category}")
            current_category = category

        total_files += 1

        if 'url' not in item or 'local_path' not in item:
            print(f"  ⚠️  Skipping item with missing url or local_path: {item}")
            failed += 1
            continue

        url = item['url']
        local_path = item['local_path']

        # Check if already exists
        if Path(local_path).exists():
            skipped += 1
            print(f"  ⏭️  Already exists: {local_path}")
        elif download_file(url, local_path):
            successful += 1
        else:
            failed += 1

    # Report unsupported category shapes so YAML issues are visible
    for category, items in sources.items():
        if not isinstance(items, list):
            print(f"⚠️  Skipping non-list category: {category}")
    
    # Print summary
    print("=" * 60)
    print("📊 Download Summary:")
    print(f"   Total files: {total_files}")
    print(f"   ✅ Successfully downloaded: {successful}")
    print(f"   ⏭️  Already existed: {skipped}")
    print(f"   ❌ Failed: {failed}")
    print("=" * 60)
    
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
