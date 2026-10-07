"""仅清理项目 Python 和 pytest 缓存，不处理资料或运行依赖。"""
from pathlib import Path
import shutil

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRECTORIES = {".venv-v3", ".venv", "node_modules", ".git"}

def clean_project_cache():
    import os
    removed = 0
    for current, directories, _ in os.walk(PROJECT_ROOT, topdown=True):
        directories[:] = [name for name in directories if name not in EXCLUDED_DIRECTORIES]
        if "__pycache__" in directories:
            shutil.rmtree(Path(current) / "__pycache__")
            directories.remove("__pycache__")
            removed += 1
    cache = PROJECT_ROOT / ".pytest_cache"
    if cache.exists():
        shutil.rmtree(cache)
        removed += 1
    print(f"已清理 {removed} 个项目缓存目录")

if __name__ == "__main__":
    clean_project_cache()
