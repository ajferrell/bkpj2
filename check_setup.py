"""
Setup checker: verify environment and dependencies.
"""

import sys
from pathlib import Path


def check_python_version():
    """Check Python version."""
    version = sys.version_info
    print(f"✓ Python {version.major}.{version.minor}.{version.micro}")
    
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print("  ⚠ Warning: Python 3.8+ recommended")
        return False
    return True


def check_dependencies():
    """Check required packages."""
    required = [
        'ebooklib',
        'sounddevice',
        'numpy',
        'watchdog'
    ]
    
    missing = []
    
    for pkg in required:
        try:
            __import__(pkg)
            print(f"✓ {pkg}")
        except ImportError:
            print(f"✗ {pkg} (missing)")
            missing.append(pkg)
    
    if missing:
        print(f"\nInstall missing packages with:")
        print(f"  pip install -r requirements.txt")
        return False
    
    return True


def check_directories():
    """Check required directories."""
    dirs = ['data', 'audio']
    
    for d in dirs:
        path = Path(d)
        if path.exists():
            print(f"✓ {d}/")
        else:
            print(f"✗ {d}/ (missing)")
            path.mkdir(exist_ok=True)
            print(f"  → Created {d}/")
    
    return True


def check_config():
    """Check config file."""
    config_path = Path('config.json')
    
    if config_path.exists():
        print(f"✓ config.json")
        
        try:
            import json
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Check scene bins
            scene_bins = config.get('scene_bins', {})
            if len(scene_bins) == 6:
                print(f"  → {len(scene_bins)} scene bins configured")
            else:
                print(f"  ⚠ Expected 6 scene bins, found {len(scene_bins)}")
            
            return True
        
        except Exception as e:
            print(f"  ✗ Error reading config: {e}")
            return False
    else:
        print(f"✗ config.json (missing)")
        return False


def check_wav_files():
    """Check for WAV files."""
    audio_dir = Path('audio')
    wav_files = list(audio_dir.glob('*.wav'))
    
    required_scenes = ['conflict', 'tension', 'movement', 'dialogue', 'reflection', 'wonder']
    found_scenes = [f.stem for f in wav_files]
    
    print(f"\nWAV files ({len(wav_files)}/6):")
    
    for scene in required_scenes:
        if scene in found_scenes:
            print(f"  ✓ {scene}.wav")
        else:
            print(f"  ✗ {scene}.wav (missing)")
    
    if len(wav_files) < 6:
        print(f"\nGenerate placeholder files with:")
        print(f"  python generate_placeholders.py")
        return False
    
    return True


def check_calibre_path():
    """Check Calibre annotations path."""
    import os
    
    appdata = os.environ.get('APPDATA', '')
    if not appdata:
        print("✗ %APPDATA% not found")
        return False
    
    calibre_path = Path(appdata) / 'calibre' / 'viewer' / 'annots'
    
    if calibre_path.exists():
        print(f"✓ Calibre annots path found")
        
        # Count JSON files
        json_files = list(calibre_path.glob('*.json'))
        if json_files:
            print(f"  → {len(json_files)} annotation file(s) found")
        else:
            print(f"  → No annotation files yet (open a book in Calibre)")
        
        return True
    else:
        print(f"✗ Calibre annots path not found: {calibre_path}")
        print(f"  → Install Calibre or verify the path in config.json")
        return False


def main():
    """Run all checks."""
    print("=" * 60)
    print("EPUB Ambience Orchestrator - Setup Checker")
    print("=" * 60)
    print()
    
    checks = [
        ("Python Version", check_python_version),
        ("Dependencies", check_dependencies),
        ("Directories", check_directories),
        ("Configuration", check_config),
        ("WAV Files", check_wav_files),
        ("Calibre Path", check_calibre_path),
    ]
    
    results = []
    
    for name, check_func in checks:
        print(f"\n{name}:")
        print("-" * 40)
        result = check_func()
        results.append((name, result))
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status:8} {name}")
    
    print(f"\n{passed}/{total} checks passed")
    
    if passed == total:
        print("\n🎉 All checks passed! Ready to run.")
        print("\nNext steps:")
        print("  1. python main.py run --dummy    (test without Calibre)")
        print("  2. python main.py preprocess <epub_path>")
        print("  3. python main.py run            (live with Calibre)")
    else:
        print("\n⚠ Some checks failed. Please fix the issues above.")
    
    print()


if __name__ == '__main__':
    main()
