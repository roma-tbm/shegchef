
import os
import json
import shutil
from pathlib import Path
from datetime import date

def test_r6_scalars():
    print("Testing R6: JSON Scalar Protection...")
    test_dir = Path("/tmp/shegchef_r6_verify")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True)

    from kitchen.web.dataset import Dataset
    ds = Dataset.in_dir(test_dir)

    from kitchen.web import settings_store, deviation_store, status_store, report_store

    stores = [
        ('menu', lambda: ds.load_menu_days(), 'menu.json'),
        ('settings', lambda: settings_store.load_settings(ds.settings), 'settings.json'),
        ('notes', lambda: deviation_store.load_deviations(date.today(), ds.notes), 'notes.json'),
        ('statuses', lambda: status_store.load_statuses(ds.statuses), 'statuses.json'),
        ('reports', lambda: report_store.load_report(date.today(), ds.reports), 'reports.json'),
    ]

    scalars = [
        ("null", "null"),
        ("false", "false"),
        ("number", "123"),
        ("string", '"invalid"'),
    ]

    all_pass = True
    for name, loader, filename in stores:
        file_path = test_dir / filename
        for label, content in scalars:
            file_path.write_text(content)
            try:
                loader()
                print(f"  FAIL: {name} accepted {label} ({content})")
                all_pass = False
            except Exception as e:
                print(f"  PASS: {name} rejected {label} with {type(e).__name__}")

    for name, loader, filename in stores:
        (test_dir / filename).unlink(missing_ok=True)
        try:
            loader()
            print(f"  PASS: {name} handled missing file")
        except Exception as e:
            print(f"  FAIL: {name} crashed on missing file: {e}")
            all_pass = False

    return all_pass

def test_r7_persistence():
    print("\nTesting R7: Persistence Source...")
    test_dir = Path("/tmp/shegchef_r7_verify")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True)

    # Scenario 1: SHEGCHEF_MENU_FILE override
    os.environ['SHEGCHEF_DATA_DIR'] = str(test_dir)
    override_file = test_dir / "override_menu.json"
    override_file.write_text(json.dumps({"days": {}}))
    os.environ['SHEGCHEF_MENU_FILE'] = str(override_file)

    from kitchen.web.dataset import Dataset
    ds = Dataset.default()

    day_key = "2026-10-12"
    targets = ds.menu_write_targets(day_key)

    if str(override_file) in [str(p) for p in targets]:
        print("  PASS: R7.1 Override file is targeted for write")
    else:
        print(f"  FAIL: R7.1 Override file NOT targeted. Targets: {targets}")
        return False

    # Scenario 2: menu.json vs меню.json (Conflict/Preservation)
    if 'SHEGCHEF_MENU_FILE' in os.environ:
        del os.environ['SHEGCHEF_MENU_FILE']

    menu_ru = test_dir / "меню.json"
    menu_en = test_dir / "menu.json"

    # Use correct wrapper format: {"days": {...}}
    menu_ru.write_text(json.dumps({"days": {"2026-10-12": []}}))
    menu_en.write_text(json.dumps({"days": {"2026-10-13": []}}))

    ds = Dataset.default()
    targets = ds.menu_write_targets("2026-10-12")
    if str(menu_ru) in [str(p) for p in targets] and str(menu_en) not in [str(p) for p in targets]:
        print("  PASS: R7.2 Correct file targeted for existing date")
    else:
        print(f"  FAIL: R7.2 Wrong targets: {targets}")
        return False

    return True

if __name__ == "__main__":
    r6 = test_r6_scalars()
    r7 = test_r7_persistence()
    if r6 and r7:
        print("\nAll R6/R7 independent checks PASSED")
        exit(0)
    else:
        print("\nSome R6/R7 checks FAILED")
        exit(1)
