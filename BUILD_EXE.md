# RVP exe化手順(Windows)

PyInstaller の **onedir 方式**でビルドする(pygame-ce=LGPL-2.1 対応のため、
ネイティブモジュールを差し替え可能な形にする方針==進捗メモ。onefileは使わない)。
ビルドは **Windows実機で行う**(PyInstallerはクロスビルド不可)。

**かんたんビルド**: 同梱の `build_exe.bat` をダブルクリック(または
コマンドプロンプトで実行)すると、以下の 2.〜3. (ビルド+ライセンス類の
コピー)まで自動で行う。初回のみ 1. の準備が必要。

## 1. 準備(初回のみ)

```bat
py -m pip install pyinstaller
```

RVPの実行に使っている環境(customtkinter等が入っている環境)と同じPythonで入れること。

## 2. ビルド

zipの展開先(このファイルがあるフォルダ=rvp_launcher.py がある場所)で:

```bat
py -m PyInstaller rvp_launcher.py --name RVP --onedir --windowed ^
  --icon icon\rvp.ico ^
  --add-data "rvp\assets;rvp\assets" ^
  --collect-all customtkinter ^
  --collect-all tkinterdnd2 ^
  --noconfirm
```

- `--windowed`: コンソール窓を出さない。
- `--icon icon\rvp.ico`: **exeファイル自体のアイコン**(エクスプローラ表示・
  ピン留め・タスクバーのフォールバックに使われる)。
- `--add-data "rvp\assets;rvp\assets"`: 実行時アイコン(rvp/assets)を同梱。
  main.py の `_set_app_icon()` は frozen 環境でも同じ相対位置で見つけられる。
- `--collect-all customtkinter`: CTkのテーマJSON等のデータ同梱(必須)。
- `--collect-all tkinterdnd2`: D&D用のtkdndバイナリ同梱。
  **tkinterdnd2 を入れていない環境ではこの行を削る**(オプショナル依存)。
- pyserial(TCode直結)はインストール済みなら自動で入る。mpvは同梱しない
  (外部インストール方式のまま)。

## 3. ライセンス類のコピー(配布時は必須)

exe と同じフォルダ(`dist\RVP\`)へ以下をコピーする:

```bat
copy /Y LICENSE dist\RVP\
copy /Y THIRD-PARTY-LICENSES.txt dist\RVP\
copy /Y README.md dist\RVP\
```

- **LICENSE / THIRD-PARTY-LICENSES.txt は必須**: RVP本体(MIT)と同梱OSS
  (pygame-ce=LGPL-2.1 ほか)のライセンス条件を満たすための同梱物。
  アプリ内のライセンス画面も frozen 時は **exe と同じフォルダ**の
  THIRD-PARTY-LICENSES.txt を探して開くため、無いと
  「ファイルが見つかりません」表示になる。
- README.md は説明書として同梱推奨(必須ではない)。

## 4. 成果物と起動確認

- `dist\RVP\RVP.exe`(+ `_internal` フォルダ+上記コピー分)。
  **フォルダごと1セット**なので exe単体を移動しない。
- 起動して確認: ヘルプ→ライセンス画面の先頭「バージョン X.Y.Z」が
  リリース番号と一致すること、タイトルバー/タスクバーのアイコン、シナリオ再生、編集画面、
  デバイス接続、動画(mpv)、D&D、**ヘルプ→ライセンス→
  「THIRD-PARTY-LICENSES.txt を開く」が開けること**。

## 5. GitHub Releases への配置

1. `dist\RVP\` フォルダを **フォルダごと** zip にする
   (例: エクスプローラで RVP フォルダを右クリック→「ZIPファイルに圧縮」。
   zip名は `RVP_vx.x.x-win64.zip` のように **公開バージョン(`rvp/__init__.py`
   の `__version__`)** 入りにする。GitHub のタグも同じ番号 `v1.0.0`)。
   zip内は `RVP\RVP.exe` / `RVP\_internal\...` / `RVP\LICENSE` /
   `RVP\THIRD-PARTY-LICENSES.txt` / `RVP\README.md` という構成になる。
2. GitHub のリポジトリ → Releases → Draft a new release → タグ・タイトル・
   説明を書き、この zip を Assets へアップロードして Publish。
3. 説明欄には最低限「Windows用・展開して RVP.exe を実行」「ウイルス対策
   ソフトが誤検知することがある(PyInstaller製exeの既知事象)」を書く。

## 6. ハマりどころ

- **`build_exe.bat` は CP932(Shift-JIS)+CRLF で保存されていること**。
  UTF-8/LF で保存し直すと、ダブルクリックしても何も実行されずに閉じる
  (cmd.exe が `rem` の日本語を CP932 として誤読し行末を巻き込む/LF では
  `goto :fail` のラベルを見つけられない)。リポジトリでは `.gitattributes`
  (`*.bat text eol=crlf`)で改行を固定している。エディタで直すときは
  「Shift-JIS / CRLF」で上書き保存する。

- **アイコンが変わって見えないときはWindowsのアイコンキャッシュ**を疑う。
  dist フォルダを別名にして起動し直すのが一番早い確認方法。
- PyInstallerのexeは**ウイルス対策ソフトが誤検知することがある**(既知・README注意書き予定)。
- ビルド生成物(`build\` / `dist\` / `RVP.spec`)はリポジトリに入れない(.gitignore対象=公開準備で作成)。
