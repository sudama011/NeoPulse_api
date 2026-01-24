import sys
import os
import logging

# 1. Setup Path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.insert(0, project_root)

# 2. Setup Logger
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("Verifier")

def test_import(module_name):
    try:
        __import__(module_name)
        logger.info(f"✅ Import OK: {module_name}")
        return True
    except ImportError as e:
        logger.error(f"❌ Import FAILED: {module_name}")
        logger.error(f"   Reason: {e}")
        return False
    except Exception as e:
        logger.error(f"⚠️ Critical Error in {module_name}: {e}")
        return False

if __name__ == "__main__":
    print("--- 🏗️ Verifying Architecture ---")
    
    all_passed = True
    
    # List of Critical Modules to Check
    modules_to_check = [
        "app.core.events",
        "app.core.settings",
        "app.adapters.kotak.client",
        "app.db.base",
        # New Modules
        "app.modules.risk.capital",
        "app.modules.risk.rules",
        "app.modules.oms.execution",
        "app.modules.oms.taxes",
        "app.modules.strategy.base",
        "app.modules.strategy.engine",
        "app.modules.strategy.indicators",
        # Specific Strategies
        "app.modules.strategy.lib.momentum"
    ]

    for mod in modules_to_check:
        if not test_import(mod):
            all_passed = False

    print("-----------------------------------")
    if all_passed:
        print("🎉 SUCCESS: All modules represent the new structure!")
    else:
        print("💥 FAILURE: Some modules have broken imports. Check the logs above.")