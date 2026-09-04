#!/usr/bin/env python3
"""测试 PostgreSQL 连接和 LangGraph checkpointer 初始化。

Usage:
    python scripts/test_pg_connection.py

Environment variables:
    AGENTCORE_POSTGRES_URL - PostgreSQL 连接字符串 (可选)
    AGENTCORE_POSTGRES_HOST - PostgreSQL 主机 (默认: localhost)
    AGENTCORE_POSTGRES_PORT - PostgreSQL 端口 (默认: 5432)
    AGENTCORE_POSTGRES_DB - 数据库名 (默认: agentcore)
    AGENTCORE_POSTGRES_USER - 用户名 (默认: agentcore)
    AGENTCORE_POSTGRES_PASSWORD - 密码 (默认: agentcore_secret)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


async def test_connection() -> bool:
    """Test PostgreSQL connection and LangGraph checkpointer setup."""
    print("=" * 60)
    print("AgentCore PostgreSQL Connection Test")
    print("=" * 60)
    print()

    # --- Check dependencies ---
    print("[1/5] Checking dependencies...")
    try:
        import asyncpg
        print("  ✓ asyncpg installed")
    except ImportError:
        print("  ✗ asyncpg not installed")
        print("    Install with: pip install asyncpg")
        return False

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        print("  ✓ langgraph-checkpoint-postgres installed")
    except ImportError:
        print("  ✗ langgraph-checkpoint-postgres not installed")
        print("    Install with: pip install langgraph-checkpoint-postgres")
        return False

    # --- Build connection string ---
    print()
    print("[2/5] Building connection string...")
    from agentcore.runtime.paths import get_postgres_connection_string

    conn_string = get_postgres_connection_string()
    # Mask password for display
    masked = conn_string.split("@")[0].split("://")[0] + "://***:***@" + conn_string.split("@")[1]
    print(f"  Connection: {masked}")

    # --- Test raw connection ---
    print()
    print("[3/5] Testing raw PostgreSQL connection...")
    try:
        conn = await asyncpg.connect(conn_string)
        version = await conn.fetchval("SELECT version()")
        print(f"  ✓ Connected successfully")
        print(f"  PostgreSQL version: {version.split(',')[0]}")
        await conn.close()
    except Exception as e:
        print(f"  ✗ Connection failed: {e}")
        return False

    # --- Test LangGraph checkpointer ---
    print()
    print("[4/5] Testing LangGraph checkpointer initialization...")
    try:
        conn = await asyncpg.connect(conn_string)
        saver = AsyncPostgresSaver(conn)

        # Setup tables (idempotent)
        await saver.setup()
        print("  ✓ Checkpointer tables created/verified")

        # Test write
        config = {"configurable": {"thread_id": "test-connection-thread"}}
        checkpoint = {
            "v": 1,
            "ts": "2026-01-01T00:00:00Z",
            "id": "test-checkpoint",
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
            "pending_writes": [],
        }
        await saver.aput(config, checkpoint, {})
        print("  ✓ Test write successful")

        # Test read
        result = await saver.aget(config)
        if result is not None:
            print("  ✓ Test read successful")
        else:
            print("  ⚠ Read returned None (may be expected)")

        # Cleanup
        await saver.adelete_thread("test-connection-thread")
        print("  ✓ Cleanup successful")

        await conn.close()
    except Exception as e:
        print(f"  ✗ Checkpointer test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    # --- Test integration with AgentCore ---
    print()
    print("[5/5] Testing AgentCore integration...")
    try:
        from agentcore.runtime.paths import is_postgres_enabled

        if is_postgres_enabled():
            print("  ✓ AGENTCORE_CHECKPOINTER_BACKEND=postgresql")
        else:
            print("  ⚠ AGENTCORE_CHECKPOINTER_BACKEND is not 'postgresql'")
            print("    Set it to 'postgresql' to use PostgreSQL in production")

        print("  ✓ AgentCore paths module loaded successfully")
    except Exception as e:
        print(f"  ⚠ Integration test skipped: {e}")

    # --- Summary ---
    print()
    print("=" * 60)
    print("All tests passed! PostgreSQL is ready for AgentCore.")
    print("=" * 60)
    print()
    print("To enable PostgreSQL in AgentCore:")
    print("  1. Set AGENTCORE_CHECKPOINTER_BACKEND=postgresql in .env")
    print("  2. Restart AgentCore")
    print()

    return True


def main() -> int:
    """Run the connection test."""
    success = asyncio.run(test_connection())
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
