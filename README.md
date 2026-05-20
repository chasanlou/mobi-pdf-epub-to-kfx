# Manga to KFX Studio

一个 Windows 桌面小工具，用来把漫画类文件批量转换成 Kindle 可用的 KFX 文件。

支持拖入：

- MOBI 文件
- EPUB 文件
- PDF 文件
- 已经整理好的图片文件夹

程序会按顺序完成图片提取、KCC 转 CBZ、kckfxgen 转 KFX，并在最终 KFX 成功生成后清理中间图片目录和 CBZ 文件。直接拖入的原始图片文件夹会被保留。

## 功能

- 淡色系 PySide6 图形界面
- 支持批量拖入
- 支持中断转换
- 支持主题颜色记忆
- 支持设置 KCC 路径
- 支持设置图片提取目录、CBZ 中间目录、最终 KFX 输出目录
- KFX 元数据按文件名解析：
  - `作品名-作者`
  - `-` 前面的全部内容作为作品名
  - `-` 后面的全部内容作为作者
  - 没有 `-` 时只写作品名，不写作者

## 依赖

本项目依赖本机已有的外部工具：

- Kindle Comic Converter / KCC
- Kindle Previewer 3 附带的 KCC 环境
- kckfxgen CLI runtime
- Python 3.10
- Python 包：`PySide6`、`mobi`、`PyMuPDF`


## 默认路径

程序内置默认路径如下，首次运行后可以在设置页修改：

```text
KCC 程序：
C:\Kindle Previewer 3\lib\fc\bin\KCC_10.1.3.exe

图片提取目录：
E:\Maga_Output

CBZ 中间目录：
E:\Maga_Output\cbz

最终 KFX 输出目录：
D:\漫画
```



## 开发运行

在安装好依赖后，可以直接运行：

```powershell
python MobiKfxStudio.pyw
```

或者运行流水线脚本：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\MobiToKfxPipeline.ps1 -NoMessageBox "D:\path\book.mobi"
```

## 打包

使用 PyInstaller 打包：

```powershell
python -m PyInstaller --noconfirm --clean --windowed --name MobiKfxStudio --add-data "MobiToKfxPipeline.ps1;." --add-data "extract_mobi_images.py;." --add-data "batch_kfxgen.py;." MobiKfxStudio.pyw
```

生成结果在：

```text
dist\MobiKfxStudio\MobiKfxStudio.exe
```



## 注意

- KCC 仍然需要通过 GUI 自动化操作，所以转换阶段可能会短暂切换窗口焦点。
- PDF 会先渲染成图片，文件较大时提取时间会比较久。
- KFX 生成依赖 kckfxgen runtime，Python 版本不匹配会导致 `bad magic number` 一类错误。
- KCC的输出路径需要自己修改为程序的cbz输出路径一致，否则会出错
