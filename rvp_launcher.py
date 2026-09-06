# PyInstaller用エントリポイント(=146)。
# 通常起動は従来どおり `python -m rvp.main` / `py -m rvp.main`。
# exe化の手順は BUILD_EXE.md を参照。
from rvp.main import main

if __name__ == "__main__":
    main()
