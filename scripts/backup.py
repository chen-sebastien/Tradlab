"""Online SQLite backup. Restore only with all project processes stopped."""
import argparse, sqlite3, shutil
from pathlib import Path
from backend.config import DB,LOCAL

def main():
    p=argparse.ArgumentParser();p.add_argument('operation',choices=['backup','restore']);p.add_argument('file',type=Path);a=p.parse_args()
    if a.operation=='backup':
        if a.file.exists(): raise ValueError('目的檔案已存在；請使用新檔名')
        with sqlite3.connect(DB) as src,sqlite3.connect(a.file) as dst: src.backup(dst)
        print('備份完成；模型 data/models 請一併另存。權杖不包含於資料庫。')
    else:
        if (LOCAL/'supervisor.json').exists(): raise ValueError('請先停止專案服務')
        with sqlite3.connect(f'file:{a.file.as_posix()}?mode=ro',uri=True) as src:
            if src.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('備份損毀')
            if DB.exists(): shutil.copy2(DB,DB.with_suffix('.pre-restore.sqlite3'))
            with sqlite3.connect(DB) as dst: src.backup(dst)
        print('已還原；啟動前執行 alembic upgrade head。原始 DB 已保存。')

if __name__=='__main__': main()
