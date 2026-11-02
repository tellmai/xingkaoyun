# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules
from importlib.metadata import distributions

root = Path(SPECPATH)
datas = [(str(root / 'profiles'), 'profiles'), (str(root / 'README.md'), '.'),
         (str(root / 'yunkaoxing/assets/ui'), 'yunkaoxing/assets/ui'),
         (str(root / 'THIRD_PARTY_NOTICES.md'), '.')]
for distribution in distributions():
    for item in distribution.files or []:
        parts = [str(p).lower() for p in item.parts]
        if any(p.endswith('.dist-info') for p in parts) and any('license' in p or p in ('copying', 'notice', 'authors') for p in parts):
            file = Path(distribution.locate_file(item))
            if file.is_file():
                datas.append((str(file), 'licenses/' + distribution.metadata['Name']))
a = Analysis([str(root / 'main.py')], pathex=[str(root)],
    datas=datas,
    hiddenimports=collect_submodules('pywinauto') + ['pythoncom', 'pywintypes', 'win32crypt', 'comtypes', 'comtypes.client'],
    excludes=['tkinter', 'matplotlib', 'IPython', 'notebook', 'pytest'], noarchive=False)
a.binaries = [entry for entry in a.binaries
    if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icuin.dll', 'icudt78.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='YunKaoXing',
    debug=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='YunKaoXing')
