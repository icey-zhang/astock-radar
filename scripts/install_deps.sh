#!/usr/bin/env bash
# 依赖安装脚本,处理 Claude Code Routine / Debian 系环境的常见坑
#
# 常见坑:
#   1. 老版 akshare 依赖已无维护的 `jsonpath` 包,构建时报
#      `AttributeError: install_layout. Did you mean: 'install_platlib'?`
#   2. 系统 pip/setuptools 过旧,PEP 517 构建路径失败
#   3. PEP 668 "externally-managed-environment" 阻止装包
#
# 解决思路:先升 pip+setuptools+wheel,再装 akshare 新版(>=1.14,已切到 jsonpath-ng)

set -e

# 判断是否需要 --break-system-packages(PEP 668 标记的环境需要,venv 里则不需要)
PIP_ARGS="--upgrade --quiet"
if python3 -c "import sysconfig, os; exit(0 if os.path.exists(os.path.join(sysconfig.get_path('stdlib'), 'EXTERNALLY-MANAGED')) else 1)" 2>/dev/null; then
    PIP_ARGS="$PIP_ARGS --break-system-packages"
    echo "[info] 检测到 PEP 668 externally-managed,自动启用 --break-system-packages"
fi

echo "=== [1/3] 升级 pip / setuptools / wheel (防 install_layout 错误) ==="
# --ignore-installed 让 pip 忽略系统已装版本(dpkg 装的 wheel 没有 RECORD,pip 卸载会炸)
python3 -m pip install $PIP_ARGS --ignore-installed \
    "pip>=23.0" "setuptools>=70.0" "wheel>=0.42"

echo ""
echo "=== [2/3] 安装项目依赖 ==="
python3 -m pip install $PIP_ARGS -r requirements.txt

echo ""
echo "=== [3/3] 验证关键包 ==="
python3 -c "
import sys
mods = ['akshare', 'pandas', 'numpy', 'yaml']
fail = []
for m in mods:
    try:
        pkg = __import__(m)
        ver = getattr(pkg, '__version__', '?')
        print(f'  ✓ {m} {ver}')
    except Exception as e:
        print(f'  ✗ {m}: {e}')
        fail.append(m)
sys.exit(1 if fail else 0)
"
echo ""
echo "安装完成。"
