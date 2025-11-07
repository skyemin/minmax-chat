#!/usr/bin/env python
"""
MiniMax Chat Web服务启动脚本
"""
import uvicorn
import sys

if __name__ == "__main__":
    print("=" * 70)
    print("🚀 MiniMax Chat Web服务启动中...")
    print("=" * 70)
    print("📍 访问地址: http://localhost:8000")
    print("📍 健康检查: http://localhost:8000/api/health")
    print("=" * 70)
    print("\n按 Ctrl+C 停止服务\n")
    
    try:
        uvicorn.run(
            "app:app",
            host="0.0.0.0",
            port=8000,
            reload=True,  # 开发模式下自动重载
            log_level="info"
        )
    except KeyboardInterrupt:
        print("\n\n服务已停止")
        sys.exit(0)

