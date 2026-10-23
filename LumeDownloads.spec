# Build two entry points sharing one runtime. The browser host requires standard I/O.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH)
media_data = collect_data_files('yt_dlp', includes=['extractor/youtube/jsc/_builtin/vendor/*.js'])
media_data += copy_metadata('yt-dlp-ejs')
a = Analysis([str(root / 'launch.py')], pathex=[str(root)], binaries=[], datas=media_data,
             hiddenimports=['yt_dlp_ejs.yt.solver'], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[])
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LumeDownloads',
          debug=False, strip=False, upx=False, console=False,
          version=str(root / 'installer/application-version.txt'))
host = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LumeNativeHost',
           debug=False, strip=False, upx=False, console=True,
           version=str(root / 'installer/application-version.txt'))
coll = COLLECT(gui, host, a.binaries, a.datas, strip=False, upx=False, name='LumeDownloads')
