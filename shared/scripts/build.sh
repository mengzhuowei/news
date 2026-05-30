#!/bin/bash
# 构建脚本 — 执行抓取和站点构建

set -euo pipefail

echo "=== Starting fetch and build ==="

# 执行抓取（单次运行）
python -m fetcher.scheduler --once

# 构建静态站点
python -c "
import os
from pathlib import Path
from fetcher.site_builder import SiteBuilder

output_dir = Path(os.getenv('DIST_DIR', 'dist'))
builder = SiteBuilder(
    content_dir=Path('shared-content'),
    output_dir=output_dir,
    use_db=True,
)
success = builder.build_all()
if success:
    print('Build completed successfully')
else:
    print('Build failed')
    exit(1)
"

echo "=== Build complete ==="
