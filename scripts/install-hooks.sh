#!/bin/sh
# .git/hooks 不入库，新克隆或换机器后跑一次本脚本装钩子。
root=$(git rev-parse --show-toplevel) || exit 1
cd "$root" || exit 1

cp scripts/hooks/pre-push .git/hooks/pre-push || exit 1
chmod +x .git/hooks/pre-push 2>/dev/null || true

echo "已安装 pre-push 钩子：推送前会跑 backend/tests，失败则阻止推送"
echo "绕过：git push --no-verify"
