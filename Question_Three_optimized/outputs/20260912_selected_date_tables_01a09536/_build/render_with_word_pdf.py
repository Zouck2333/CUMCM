"""Use Word's native PDF export with the canonical skill rasterizer.

The installed runtime has no bundled LibreOffice on Windows. The only substituted
step is DOCX-to-PDF conversion, performed by Microsoft Word in export_word_pdf.ps1.
"""
import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = Path(r'C:\Users\31293\.codex\plugins\cache\openai-primary-runtime\documents\26.909.12148\skills\documents\render_docx.py')
POPPLER = Path(r'C:\Users\31293\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin')
os.environ['PATH'] = str(POPPLER) + os.pathsep + os.environ['PATH']
spec = importlib.util.spec_from_file_location('canonical_render_docx', SKILL)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
pdf = HERE/'word_render.pdf'
assert pdf.exists() and pdf.stat().st_size > 0
renderer.convert_to_pdf = lambda *a, **kw: (str(pdf), 'DOCX rendered by Microsoft Word ExportAsFixedFormat')
pages = renderer.rasterize(str(HERE.parent/'第三问优化结果_表1表2表3.docx'), str(HERE/'docx_render'), 150, False, False)
print(json.dumps({'pages': len(pages), 'renderer': 'Microsoft Word + canonical render_docx.py rasterizer'}, ensure_ascii=False))
