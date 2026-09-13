"""Render final workbooks in an isolated process and verify every PNG."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import struct
import zlib

from .run_question_four import _runtime, _link_modules


def validate_png(path):
    content=path.read_bytes()
    assert content[:8]==b'\x89PNG\r\n\x1a\n'
    offset=8;tags=[];compressed=[]
    while offset<len(content):
        length=struct.unpack('>I',content[offset:offset+4])[0]
        tag=content[offset+4:offset+8];payload=content[offset+8:offset+8+length]
        crc=struct.unpack('>I',content[offset+8+length:offset+12+length])[0]
        assert zlib.crc32(tag+payload)==crc
        tags.append(tag)
        if tag==b'IDAT':compressed.append(payload)
        offset+=length+12
    assert tags[0]==b'IHDR' and tags[-1]==b'IEND' and offset==len(content)
    assert len(zlib.decompress(b''.join(compressed)))>0
    return len(content)


def main():
    package=Path(__file__).resolve().parents[1]
    output=package/'output'
    node,modules=_runtime();report={}
    with tempfile.TemporaryDirectory(prefix='q4_render_') as tmp:
        tmp=Path(tmp);_link_modules(tmp/'node_modules',modules)
        script=tmp/'output_xlsx.mjs';shutil.copy2(Path(__file__).with_name('output_xlsx.mjs'),script)
        for strategy in ('4-2','4-3'):
            folder=output/'previews'/strategy
            completed=subprocess.run([str(node),str(script),'--preview',str(output/f'result{strategy}.xlsx'),str(folder)],
                                     capture_output=True,encoding='utf-8',errors='replace',timeout=120)
            expected=3 if strategy=='4-2' else 4
            files=list(folder.glob('*.png'))
            assert f'Q4_PREVIEWS_WRITTEN:{expected}' in completed.stdout,completed.stderr
            assert len(files)==expected
            sizes={p.name:validate_png(p) for p in files}
            # Rendering is complete before the marker. A known Windows native
            # teardown failure is recorded, never treated as a clean exit.
            if completed.returncode not in (0,3221226505,-1073740791):
                raise RuntimeError(f'render failed: {completed.returncode} {completed.stderr}')
            report[strategy]={'images':sizes,'render_complete_marker':True,
                              'process_exit_code':completed.returncode,
                              'png_crc_and_decompression_verified':True,
                              'note':'Native process exit after completed writes; PNGs verified independently.' if completed.returncode else 'Clean process exit.'}
    (output/'render_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
