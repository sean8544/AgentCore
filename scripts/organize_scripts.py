#!/usr/bin/env python3
"""整理 scripts 目录 - 删除临时测试脚本

删除的临时脚本：
- e2e_*.py - 临时测试
- probe_*.py - 临时调试
- test_*.py - 临时 API 测试
- verify_*.py - 临时验证
- take_screenshots.py - 临时截图
- upload_excel.py - 硬编码路径的临时脚本
- cleanup_temp.py - 临时清理脚本

保留的正式脚本：
- install.* - 安装脚本
- start-*.* - 启动脚本  
- stop-*.* - 停止脚本
- docker_build.sh - Docker 构建
- opensandbox-quickstart.* - OpenSandbox 快速启动
- test_pg_connection.py - PostgreSQL 连接测试
"""

import sys
import io
import argparse
from pathlib import Path

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def cleanup():
    parser = argparse.ArgumentParser(description='整理 scripts 目录')
    parser.add_argument('--yes', '-y', action='store_true', help='自动确认删除')
    args = parser.parse_args()
    scripts_dir = Path("scripts")
    
    if not scripts_dir.exists():
        print("[错误] scripts 目录不存在")
        return
    
    # 要删除的临时脚本模式
    temp_patterns = [
        "e2e_*.py",
        "probe_*.py", 
        "verify_*.py",
        "take_screenshots.py",
        "upload_excel.py",
        "cleanup_temp.py",
    ]
    
    # 要删除的临时 test_* 脚本（保留 test_pg_connection.py）
    temp_test_scripts = [
        "test_chat_44.py",
        "test_chat_api.py",
        "test_files_api.py",
    ]
    
    print("=" * 60)
    print("整理 scripts 目录")
    print("=" * 60)
    
    files_to_delete = []
    
    # 查找匹配的文件
    for pattern in temp_patterns:
        files_to_delete.extend(scripts_dir.glob(pattern))
    
    for script_name in temp_test_scripts:
        script_path = scripts_dir / script_name
        if script_path.exists():
            files_to_delete.append(script_path)
    
    # 去重
    files_to_delete = list(set(files_to_delete))
    
    print(f"\n找到 {len(files_to_delete)} 个临时脚本：\n")
    
    for f in sorted(files_to_delete):
        size = f.stat().st_size
        print(f"  - {f.name} ({size} bytes)")
    
    print("\n" + "=" * 60)
    if not args.yes:
        confirm = input("确认删除这些文件？(y/N): ").strip().lower()
    else:
        confirm = 'y'
        print("[自动模式] -y 参数已传入，自动确认删除")
    
    if confirm == 'y':
        deleted = 0
        for f in files_to_delete:
            try:
                f.unlink()
                print(f"[已删除] {f.name}")
                deleted += 1
            except Exception as e:
                print(f"[失败] {f.name}: {e}")
        
        print(f"\n[完成] 成功删除 {deleted}/{len(files_to_delete)} 个文件")
    else:
        print("\n[取消] 未删除任何文件")
    
    # 显示保留的文件
    print("\n" + "=" * 60)
    print("保留的正式脚本：\n")
    
    remaining = sorted(scripts_dir.glob("*"))
    for f in remaining:
        if f.is_file():
            size = f.stat().st_size
            print(f"  ✅ {f.name} ({size} bytes)")
    
    print("\n" + "=" * 60)
    print("整理完成！")

if __name__ == "__main__":
    cleanup()
